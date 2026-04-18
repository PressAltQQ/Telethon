"""Tests for mcp_bridge/tools/bridge.py — send_message and ask_user."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_bridge.errors import (
    AskSendFailedError,
    AskTimeoutError,
    NotWhitelistedError,
    RateLimitError,
)
from mcp_bridge.errors import (
    FloodWaitError as BridgeFloodWaitError,
)
from mcp_bridge.rate_limit import reset_rate_limiter


def _config(write_chats=None, ask_chats=None, ask_fallback="strict"):
    return SimpleNamespace(
        write_chats=write_chats or [100],
        ask_chats=ask_chats or [100],
        read_chats=[100],
        channels=[],
        max_ops_per_minute=600,  # high rate so tests don't hit limit
        burst=100,
        ask_fallback=ask_fallback,
        fallback_window_seconds=120,
    )


def _fake_message(msg_id=42):
    import datetime
    msg = MagicMock()
    msg.id = msg_id
    msg.date = datetime.datetime.now(datetime.timezone.utc)
    return msg


@pytest.fixture(autouse=True)
def _reset_limiter():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


# ─── send_message ─────────────────────────────────────────────────────────────


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_rejects_non_whitelisted_chat(self):
        from mcp_bridge.tools.bridge import send_message
        client = MagicMock()
        cfg = _config(write_chats=[100])
        with pytest.raises(NotWhitelistedError):
            await send_message(client, cfg, chat_id=999, text="hi")

    @pytest.mark.asyncio
    async def test_consumes_rate_limit_token(self):
        from mcp_bridge.tools.bridge import send_message
        client = AsyncMock()
        client.send_message.return_value = _fake_message(1)
        cfg = _config()

        bucket_mock = MagicMock()
        bucket_mock.acquire = AsyncMock()

        with patch("mcp_bridge.tools.bridge.get_rate_limiter", return_value=bucket_mock):
            await send_message(client, cfg, chat_id=100, text="hi")

        bucket_mock.acquire.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_message_id_and_date(self):
        import datetime

        from mcp_bridge.tools.bridge import send_message

        client = AsyncMock()
        msg = MagicMock()
        msg.id = 77
        msg.date = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        client.send_message.return_value = msg
        cfg = _config()

        result = await send_message(client, cfg, chat_id=100, text="hello")
        assert result["message_id"] == 77

    @pytest.mark.asyncio
    async def test_translates_flood_wait_to_bridge_error(self):
        from mcp_bridge.tools.bridge import send_message

        # Create a fake Telethon FloodWaitError
        class FakeTelethonFloodWait(Exception):
            seconds = 30

        FakeTelethonFloodWait.__name__ = "FloodWaitError"

        client = AsyncMock()
        client.send_message.side_effect = FakeTelethonFloodWait("FloodWait")
        cfg = _config()

        with pytest.raises(BridgeFloodWaitError) as exc_info:
            await send_message(client, cfg, chat_id=100, text="hi")

        assert exc_info.value.retry_after_seconds == 30


# ─── ask_user ─────────────────────────────────────────────────────────────────


def _make_correlation_db(tmp_path):
    from mcp_bridge.correlation import Correlation
    corr = Correlation(str(tmp_path / "corr.db"))
    corr.open()
    return corr


def _inject_reply(corr, token, msg_id=200, text="reply text", reply_to=99):
    """Simulate an inbound message that matches the pending ask."""
    import datetime

    msg = SimpleNamespace(
        id=msg_id,
        text=text,
        message=text,
        reply_to_msg_id=reply_to,
        from_id=55,
        date=datetime.datetime.now(datetime.timezone.utc),
    )
    # We need to know the chat_id; get it from DB
    row = corr._db.execute(
        "SELECT chat_id, outbound_msg_id FROM pending_asks WHERE ask_token=?", (token,)
    ).fetchone()
    corr.match_reply(row["chat_id"], msg)


class TestAskUser:
    @pytest.mark.asyncio
    async def test_happy_path_returns_reply(self, tmp_path):
        from mcp_bridge.tools.bridge import ask_user

        corr = _make_correlation_db(tmp_path)
        client = AsyncMock()
        client.send_message.return_value = _fake_message(99)
        cfg = _config()

        async def deliver_reply():
            await asyncio.sleep(0.05)
            # Find the token
            row = corr._db.execute(
                "SELECT ask_token FROM pending_asks WHERE state='pending'"
            ).fetchone()
            if row:
                _inject_reply(corr, row["ask_token"], reply_to=99)

        asyncio.ensure_future(deliver_reply())

        result = await ask_user(
            client, corr, cfg, chat_id=100, text="question?", timeout_sec=2.0
        )
        assert "reply_text" in result
        assert result["reply_text"] == "reply text"
        assert result["match_strategy"] == "reply"
        assert "ask_token" in result
        corr.close()

    @pytest.mark.asyncio
    async def test_rejects_non_whitelisted_chat(self, tmp_path):
        from mcp_bridge.tools.bridge import ask_user

        corr = _make_correlation_db(tmp_path)
        client = AsyncMock()
        cfg = _config(ask_chats=[100])

        with pytest.raises(NotWhitelistedError):
            await ask_user(client, corr, cfg, chat_id=999, text="hi", timeout_sec=1.0)
        corr.close()

    @pytest.mark.asyncio
    async def test_timeout_raises_ask_timeout_error(self, tmp_path):
        from mcp_bridge.tools.bridge import ask_user

        corr = _make_correlation_db(tmp_path)
        client = AsyncMock()
        client.send_message.return_value = _fake_message(99)
        cfg = _config()

        with pytest.raises(AskTimeoutError):
            await ask_user(
                client, corr, cfg, chat_id=100, text="q?", timeout_sec=0.1
            )
        corr.close()

    @pytest.mark.asyncio
    async def test_send_failure_leaves_no_orphaned_row(self, tmp_path):
        """I-3: send FIRST — if send fails, no pending row is inserted in the DB."""
        from mcp_bridge.tools.bridge import ask_user

        corr = _make_correlation_db(tmp_path)
        client = AsyncMock()
        client.send_message.side_effect = RuntimeError("network error")
        cfg = _config()

        with pytest.raises(AskSendFailedError):
            await ask_user(client, corr, cfg, chat_id=100, text="q?", timeout_sec=2.0)

        # No pending row should exist — the send failed before insert_pending was called
        row = corr._db.execute(
            "SELECT state FROM pending_asks"
        ).fetchone()
        assert row is None, "No row should be inserted when send_message fails before insert_pending"
        corr.close()

    @pytest.mark.asyncio
    async def test_rate_limit_consumed_once_at_send_time(self, tmp_path):
        """Rate-limit token consumed exactly once (at send), not during wait."""
        from mcp_bridge.tools.bridge import ask_user

        corr = _make_correlation_db(tmp_path)
        client = AsyncMock()
        client.send_message.return_value = _fake_message(99)
        cfg = _config()

        bucket_mock = MagicMock()
        bucket_mock.acquire = AsyncMock()

        async def deliver_reply():
            await asyncio.sleep(0.05)
            row = corr._db.execute(
                "SELECT ask_token FROM pending_asks WHERE state='pending'"
            ).fetchone()
            if row:
                _inject_reply(corr, row["ask_token"], reply_to=99)

        asyncio.ensure_future(deliver_reply())

        with patch("mcp_bridge.tools.bridge.get_rate_limiter", return_value=bucket_mock):
            await ask_user(
                client, corr, cfg, chat_id=100, text="q?", timeout_sec=2.0
            )

        # acquire must have been called exactly once
        assert bucket_mock.acquire.call_count == 1
        corr.close()

    @pytest.mark.asyncio
    async def test_rate_limit_error_does_not_insert_pending_row(self, tmp_path):
        """If rate limiter raises before send, no pending row should be inserted."""
        from mcp_bridge.tools.bridge import ask_user

        corr = _make_correlation_db(tmp_path)
        client = AsyncMock()
        cfg = _config()

        bucket_mock = MagicMock()
        bucket_mock.acquire = AsyncMock(side_effect=RateLimitError("too many", 5.0))

        with patch("mcp_bridge.tools.bridge.get_rate_limiter", return_value=bucket_mock):
            with pytest.raises(RateLimitError):
                await ask_user(
                    client, corr, cfg, chat_id=100, text="q?", timeout_sec=2.0
                )

        # No pending row should exist
        count = corr._db.execute("SELECT COUNT(*) FROM pending_asks").fetchone()[0]
        assert count == 0
        corr.close()

    @pytest.mark.asyncio
    async def test_user_scoped_fallback_matches_target_user(self, tmp_path):
        """ask_fallback=user_scoped: non-reply from target_user_id is matched."""
        import datetime

        from mcp_bridge.tools.bridge import ask_user

        corr = _make_correlation_db(tmp_path)
        cfg = _config(ask_fallback="user_scoped")
        # Override corr config
        corr._config = SimpleNamespace(
            ask_fallback="user_scoped", fallback_window_seconds=120
        )

        client = AsyncMock()
        client.send_message.return_value = _fake_message(99)

        async def deliver_non_reply():
            await asyncio.sleep(0.05)
            row = corr._db.execute(
                "SELECT ask_token, chat_id FROM pending_asks WHERE state='pending'"
            ).fetchone()
            if row:
                from types import SimpleNamespace as NS
                now = datetime.datetime.now(datetime.timezone.utc)
                msg = NS(
                    id=200,
                    text="user reply",
                    message="user reply",
                    reply_to_msg_id=None,
                    from_id=42,  # target_user_id
                    date=now,
                )
                corr.match_reply(row["chat_id"], msg)

        asyncio.ensure_future(deliver_non_reply())

        result = await ask_user(
            client, corr, cfg, chat_id=100, text="q?",
            timeout_sec=2.0, target_user_id=42
        )
        assert result["match_strategy"] == "fallback_a"
        corr.close()
