"""
Reconnect integration tests for poll_chat_since.

Simulates disconnect (stop of update stream) then reconnect with buffered
catch-up. Asserts sequential poll_chat_since calls return EXACTLY the full
set of messages — no duplicates and no losses.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mcp_bridge.tools import poll


@pytest.fixture(autouse=True)
def reset_poll_state():
    poll.reset_state()
    poll.set_buffer_size(256)
    yield
    poll.reset_state()
    poll.set_buffer_size(256)


def make_config(read_chats=None, poll_buffer_size=256):
    return SimpleNamespace(
        read_chats=read_chats or [2001],
        poll_buffer_size=poll_buffer_size,
    )


def make_msg(message_id, text="msg"):
    return {
        "message_id": message_id,
        "from_id": 99,
        "text": text,
        "reply_to_msg_id": None,
        "date": "2026-01-01T00:00:00",
        "has_media": False,
        "media_summary": None,
    }


class TestDisconnectBufferPersistence:
    @pytest.mark.asyncio
    async def test_buffered_messages_remain_after_simulated_disconnect(self):
        """Messages in the ring buffer persist across a simulated disconnect."""
        config = make_config()
        chat_id = 2001

        # Simulate pre-disconnect messages
        for i in range(1, 6):
            poll.ingest_message(chat_id, make_msg(i))

        # Simulate disconnect: stop update stream (nothing to do here — buffer persists)
        # In a real scenario, the Telethon reconnect triggers update catch-up
        # which re-ingests messages. Here we simply do NOT ingest duplicates.

        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=50,
            connection_id="reconnect-test",
        )

        assert len(result["messages"]) == 5
        ids = [m["message_id"] for m in result["messages"]]
        assert sorted(ids) == list(range(1, 6))


class TestCatchUpAfterReconnect:
    @pytest.mark.asyncio
    async def test_sequential_polls_return_no_duplicates_no_losses(self):
        """After reconnect, messages are injected in order and sequential
        poll calls return the complete set with no duplicates and no losses.
        """
        config = make_config()
        chat_id = 2001
        total_messages = 20

        # Phase 1: pre-disconnect messages (1–10)
        for i in range(1, 11):
            poll.ingest_message(chat_id, make_msg(i))

        # First poll — collect all pre-disconnect messages
        result1 = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=50,
            connection_id="reconnect-seq",
        )
        assert len(result1["messages"]) == 10
        next_cursor = result1["next_since_message_id"]
        assert next_cursor == 10

        # Phase 2: reconnect + catch-up (messages 11–20 injected in order)
        for i in range(11, total_messages + 1):
            poll.ingest_message(chat_id, make_msg(i))

        # Second poll — collect catch-up messages
        result2 = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=next_cursor,
            timeout_ms=50,
            connection_id="reconnect-seq",
        )
        assert len(result2["messages"]) == 10
        ids2 = {m["message_id"] for m in result2["messages"]}
        assert ids2 == set(range(11, 21))

        # Combine: verify no duplicates, no losses
        all_ids = (
            {m["message_id"] for m in result1["messages"]}
            | ids2
        )
        assert all_ids == set(range(1, total_messages + 1))

    @pytest.mark.asyncio
    async def test_no_duplicate_messages_across_reconnect(self):
        """Injecting the same message_id twice does not produce duplicates
        in poll results (deque stores both, but same ID is counted only once
        since poll collects by message_id > since_id per-call window).
        """
        config = make_config()
        chat_id = 2001

        # Initial message
        poll.ingest_message(chat_id, make_msg(1))

        result1 = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=50,
            connection_id="dup-test",
        )
        assert result1["next_since_message_id"] == 1

        # After cursor advanced to 1, re-inject message with id=1 (duplicate catch-up)
        poll.ingest_message(chat_id, make_msg(1))
        # Inject new message with id=2
        poll.ingest_message(chat_id, make_msg(2))

        result2 = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=1,  # cursor at 1, so only id > 1 returned
            timeout_ms=50,
            connection_id="dup-test",
        )
        # Only message_id=2 should appear (id=1 re-inject is at or below cursor)
        assert all(m["message_id"] > 1 for m in result2["messages"])
        ids = [m["message_id"] for m in result2["messages"]]
        assert 2 in ids

    @pytest.mark.asyncio
    async def test_incremental_polling_catches_all_messages(self):
        """Simulate a continuous polling loop that catches all 30 injected messages
        across 3 poll rounds with 10 messages each, no gaps.
        """
        config = make_config()
        chat_id = 2001
        all_collected_ids = []
        cursor = 0

        for batch in range(3):
            # Inject 10 messages
            for i in range(batch * 10 + 1, batch * 10 + 11):
                poll.ingest_message(chat_id, make_msg(i))

            result = await poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=cursor,
                timeout_ms=50,
                connection_id="incremental",
            )
            batch_ids = [m["message_id"] for m in result["messages"]]
            all_collected_ids.extend(batch_ids)
            if result["messages"]:
                cursor = result["next_since_message_id"]

        assert sorted(all_collected_ids) == list(range(1, 31))
        assert len(all_collected_ids) == len(set(all_collected_ids)), "Duplicates detected"


class TestBufferOverflowCursorLost:
    @pytest.mark.asyncio
    async def test_buffer_overflow_makes_stale_cursor_raise(self):
        """When the buffer overflows and evicts messages the cursor pointed at,
        the next poll with that stale cursor raises PollCursorLostError.
        """
        from mcp_bridge.errors import PollCursorLostError

        poll.set_buffer_size(10)
        config = make_config(read_chats=[2001], poll_buffer_size=10)
        chat_id = 2001

        # Inject first batch of messages (IDs 1–10); record stale cursor at 5
        for i in range(1, 11):
            poll.ingest_message(chat_id, make_msg(i))

        stale_cursor = 5

        # Now inject 50 more messages — the ring (size=10) now holds only IDs 51–60
        for i in range(11, 61):
            poll.ingest_message(chat_id, make_msg(i))

        # stale_cursor=5 < oldest(51) - 1 = 50 → PollCursorLostError
        with pytest.raises(PollCursorLostError):
            await poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=stale_cursor,
                timeout_ms=50,
                connection_id="overflow-test",
            )
