"""
Tests for mcp_bridge/tools/poll.py.

Covers:
- Returns immediately when buffer has newer messages
- Long-poll timeout returns empty + unchanged cursor + elapsed wait
- Second concurrent call for same (connection_id, chat_id) → PollAlreadyActiveError
- since_message_id pre-dates ring → PollCursorLostError
- Whitelist gate: non-read_chat → NotWhitelistedError
- Payload contains ONLY documented fields (SC1 isolation check)
- ingest_message wakes a waiting poll
"""
from __future__ import annotations

import asyncio
import collections
from types import SimpleNamespace

import pytest

from mcp_bridge.errors import NotWhitelistedError, PollAlreadyActiveError, PollCursorLostError
from mcp_bridge.tools import poll


@pytest.fixture(autouse=True)
def reset_poll_state():
    """Reset all poll module state between tests."""
    poll.reset_state()
    poll.set_buffer_size(256)
    yield
    poll.reset_state()
    poll.set_buffer_size(256)


def make_config(read_chats=None, poll_buffer_size=256):
    return SimpleNamespace(
        read_chats=read_chats or [1001, 1002],
        poll_buffer_size=poll_buffer_size,
    )


def make_msg(message_id, text="hello"):
    return {
        "message_id": message_id,
        "from_id": 42,
        "text": text,
        "reply_to_msg_id": None,
        "date": "2026-01-01T00:00:00",
        "has_media": False,
        "media_summary": None,
    }


class TestPollReturnsBuferedMessages:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_buffer_has_newer_messages(self):
        config = make_config()
        chat_id = 1001
        # Pre-load buffer
        poll.ingest_message(chat_id, make_msg(10))
        poll.ingest_message(chat_id, make_msg(11))

        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=9,
            timeout_ms=100,
            connection_id="conn-a",
        )

        assert len(result["messages"]) == 2
        ids = {m["message_id"] for m in result["messages"]}
        assert ids == {10, 11}
        assert result["next_since_message_id"] == 11
        assert result["server_wait_ms"] < 200  # returned quickly

    @pytest.mark.asyncio
    async def test_does_not_return_messages_at_or_below_since_id(self):
        config = make_config()
        chat_id = 1001
        poll.ingest_message(chat_id, make_msg(5))
        poll.ingest_message(chat_id, make_msg(10))

        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=10,
            timeout_ms=50,
            connection_id="conn-a",
        )

        assert result["messages"] == []
        assert result["next_since_message_id"] == 10


class TestLongPollTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty_unchanged_cursor(self):
        config = make_config()
        chat_id = 1001
        timeout_ms = 100

        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=timeout_ms,
            connection_id="conn-b",
        )

        assert result["messages"] == []
        assert result["next_since_message_id"] == 0
        # Wait should be roughly timeout_ms or slightly more
        assert result["server_wait_ms"] >= timeout_ms * 0.5


class TestConcurrencyGuard:
    @pytest.mark.asyncio
    async def test_second_concurrent_call_raises_poll_already_active(self):
        config = make_config()
        chat_id = 1001
        connection_id = "conn-concurrent"

        # Start a long poll in the background
        task = asyncio.ensure_future(
            poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=0,
                timeout_ms=2000,
                connection_id=connection_id,
            )
        )
        # Yield to let the task start and register itself
        await asyncio.sleep(0)

        # Second call should immediately raise
        with pytest.raises(PollAlreadyActiveError):
            await poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=0,
                timeout_ms=100,
                connection_id=connection_id,
            )

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_different_connection_ids_are_allowed_concurrently(self):
        config = make_config()
        chat_id = 1001

        task1 = asyncio.ensure_future(
            poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=0,
                timeout_ms=200,
                connection_id="conn-1",
            )
        )
        task2 = asyncio.ensure_future(
            poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=0,
                timeout_ms=200,
                connection_id="conn-2",
            )
        )
        # Both should complete without PollAlreadyActiveError
        results = await asyncio.gather(task1, task2)
        assert len(results) == 2


class TestCursorLost:
    @pytest.mark.asyncio
    async def test_since_id_predates_ring_raises_cursor_lost(self):
        """Non-zero cursor that is behind oldest_id - 1 raises PollCursorLostError."""
        poll.set_buffer_size(5)
        config = make_config(poll_buffer_size=5)
        chat_id = 1001

        # Fill buffer: 15 injected into 5-entry ring → oldest=110, newest=114
        for i in range(100, 115):
            poll.ingest_message(chat_id, make_msg(i))

        # since_id=5 < oldest(110) - 1 = 109 → cursor lost
        with pytest.raises(PollCursorLostError):
            await poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=5,
                timeout_ms=50,
                connection_id="conn-c",
            )

    @pytest.mark.asyncio
    async def test_since_id_zero_does_not_raise_cursor_lost(self):
        """since_message_id=0 is the bootstrap sentinel — never raises PollCursorLostError."""
        # Use set_buffer_size so ingest_message creates a 5-entry ring
        poll.set_buffer_size(5)
        config = make_config(poll_buffer_size=5)
        chat_id = 1001

        # Inject 15 messages — ring holds only the last 5 (IDs 111–115 → 110–114)
        for i in range(100, 115):
            poll.ingest_message(chat_id, make_msg(i))

        # since_id=0 must NOT raise even though 0 < oldest - 1
        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=50,
            connection_id="conn-c2",
        )
        assert len(result["messages"]) == 5  # entire 5-entry ring returned

    @pytest.mark.asyncio
    async def test_since_id_one_behind_oldest_does_not_raise(self):
        """Cursor at oldest_id - 1 is the boundary — no error, just returns all buffered."""
        poll.set_buffer_size(5)
        config = make_config(poll_buffer_size=5)
        chat_id = 1001

        # Inject 15 → ring holds IDs 110–114, oldest=110
        for i in range(100, 115):
            poll.ingest_message(chat_id, make_msg(i))

        # since_id=109 == oldest(110) - 1 → no gap, no error
        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=109,
            timeout_ms=50,
            connection_id="conn-c3",
        )
        assert len(result["messages"]) == 5

    @pytest.mark.asyncio
    async def test_since_id_within_ring_does_not_raise(self):
        config = make_config(poll_buffer_size=256)
        chat_id = 1001

        poll.ingest_message(chat_id, make_msg(100))
        poll.ingest_message(chat_id, make_msg(101))

        # since_id=99 is immediately before oldest(100) → no gap → no error
        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=99,
            timeout_ms=50,
            connection_id="conn-d",
        )
        assert len(result["messages"]) == 2

    @pytest.mark.asyncio
    async def test_cursor_lost_on_buffer_overflow(self):
        """Regression: poll_buffer_size=10, inject 50 msgs, cursor at 5 → PollCursorLostError."""
        # set_buffer_size so ingest_message creates a 10-entry ring
        poll.set_buffer_size(10)
        config = make_config(poll_buffer_size=10)
        chat_id = 1001

        # Inject 50 messages so the ring retains only IDs 41–50
        for i in range(1, 51):
            poll.ingest_message(chat_id, make_msg(i))

        # since_id=5 < oldest(41) - 1 = 40 → cursor is behind the evicted range
        with pytest.raises(PollCursorLostError):
            await poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=5,
                timeout_ms=50,
                connection_id="conn-overflow",
            )


class TestWhitelistGate:
    @pytest.mark.asyncio
    async def test_non_read_chat_raises_not_whitelisted(self):
        config = make_config(read_chats=[1001])

        with pytest.raises(NotWhitelistedError):
            await poll.poll_chat_since(
                config=config,
                chat_id=9999,  # not in read_chats
                since_message_id=0,
                timeout_ms=100,
                connection_id="conn-e",
            )

    @pytest.mark.asyncio
    async def test_read_chat_passes_whitelist(self):
        config = make_config(read_chats=[1001])

        # Should not raise
        result = await poll.poll_chat_since(
            config=config,
            chat_id=1001,
            since_message_id=0,
            timeout_ms=50,
            connection_id="conn-f",
        )
        assert "messages" in result


class TestPayloadIsolation:
    """SC1: poll response contains ONLY documented metadata fields."""

    @pytest.mark.asyncio
    async def test_payload_contains_only_documented_fields(self):
        config = make_config()
        chat_id = 1001

        # Inject message with extra fields that should NOT appear in response
        msg_with_extras = {
            "message_id": 50,
            "from_id": 42,
            "text": "hello",
            "reply_to_msg_id": None,
            "date": "2026-01-01T00:00:00",
            "has_media": False,
            "media_summary": None,
            # These must NOT appear in poll response
            "auth_key": b"\x00" * 256,
            "raw_bytes": b"sensitive",
            "_internal_state": "private",
        }
        poll.ingest_message(chat_id, msg_with_extras)

        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=49,
            timeout_ms=100,
            connection_id="conn-g",
        )

        assert len(result["messages"]) == 1
        msg = result["messages"][0]
        allowed_fields = {"message_id", "from_id", "text", "reply_to_msg_id", "date", "has_media", "media_summary"}
        extra_fields = set(msg.keys()) - allowed_fields
        assert extra_fields == set(), f"Unexpected fields in poll response: {extra_fields}"

    @pytest.mark.asyncio
    async def test_auth_key_never_in_response(self):
        config = make_config()
        chat_id = 1001

        poll.ingest_message(chat_id, {
            "message_id": 1,
            "from_id": 1,
            "text": "test",
            "reply_to_msg_id": None,
            "date": "2026-01-01",
            "has_media": False,
            "media_summary": None,
            "auth_key": b"\xde\xad\xbe\xef" * 64,
        })

        result = await poll.poll_chat_since(
            config=config, chat_id=chat_id, since_message_id=0,
            timeout_ms=50, connection_id="conn-h",
        )
        # Serialize result and check auth_key bytes do not appear
        import json
        serialized = json.dumps(result, default=str)
        assert "auth_key" not in serialized
        assert "deadbeef" not in serialized.lower()


class TestIngestWakesPoll:
    @pytest.mark.asyncio
    async def test_ingest_message_wakes_waiting_poll(self):
        config = make_config()
        chat_id = 1001

        async def inject_after_delay():
            await asyncio.sleep(0.05)
            poll.ingest_message(chat_id, make_msg(200))

        inject_task = asyncio.ensure_future(inject_after_delay())
        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=2000,  # long timeout — should wake early
            connection_id="conn-wake",
        )
        await inject_task

        # Should have been woken by the injected message
        assert len(result["messages"]) >= 1
        assert result["messages"][0]["message_id"] == 200
        # Should have returned well before the 2000ms timeout
        assert result["server_wait_ms"] < 1000


class TestBufferSize:
    def test_set_buffer_size_affects_new_buffers(self):
        """set_buffer_size() changes the maxlen used for subsequently created deques."""
        poll.set_buffer_size(10)
        chat_id = 7001

        # Inject 20 messages — buffer should only keep the last 10
        for i in range(1, 21):
            poll.ingest_message(chat_id, make_msg(i))

        buf = poll._BUFFERS[chat_id]
        assert buf.maxlen == 10
        assert len(buf) == 10
        ids = [m["message_id"] for m in buf]
        assert ids == list(range(11, 21))

    def test_set_buffer_size_does_not_resize_existing_buffers(self):
        """Existing deques keep their original maxlen after set_buffer_size()."""
        poll.set_buffer_size(256)
        chat_id = 7002

        # Create the buffer with size 256
        poll.ingest_message(chat_id, make_msg(1))
        buf = poll._BUFFERS[chat_id]
        assert buf.maxlen == 256

        # Now change the module-level size
        poll.set_buffer_size(5)

        # Existing buffer still has maxlen=256
        assert buf.maxlen == 256


class TestSortedMessages:
    @pytest.mark.asyncio
    async def test_messages_sorted_by_message_id(self):
        """Returned messages are sorted by message_id ascending."""
        config = make_config()
        chat_id = 1001

        # Inject in reverse order to simulate out-of-order catch-up
        for i in [5, 3, 1, 4, 2]:
            poll.ingest_message(chat_id, make_msg(i))

        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=50,
            connection_id="conn-sort",
        )

        ids = [m["message_id"] for m in result["messages"]]
        assert ids == sorted(ids), f"messages not sorted: {ids}"

    @pytest.mark.asyncio
    async def test_next_since_message_id_is_max_of_sorted(self):
        """next_since_message_id equals the highest message_id in sorted output."""
        config = make_config()
        chat_id = 1001

        for i in [10, 7, 15, 3]:
            poll.ingest_message(chat_id, make_msg(i))

        result = await poll.poll_chat_since(
            config=config,
            chat_id=chat_id,
            since_message_id=0,
            timeout_ms=50,
            connection_id="conn-nsmi",
        )

        assert result["next_since_message_id"] == 15
