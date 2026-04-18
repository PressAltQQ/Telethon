"""
Integration tests for ask_user wiring through dispatch_tool.

B1: Correlation is instantiated and wired into the call chain so that
    ask_user can match replies.
C3: ask_user path through dispatch_tool end-to-end.
I1: dispatch_tool emits a JSON-lines audit log entry for each call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_bridge.correlation import Correlation
from mcp_bridge.tools import poll


def make_config(tmp_path: Path, chat_id: int = 5001) -> SimpleNamespace:
    return SimpleNamespace(
        api_id=99999,
        api_hash="test_hash",
        session_name="integration_test",
        key_source="env",
        base_dir=tmp_path / "data",
        max_file_size_mb=100,
        allowed_mime_types=["application/pdf"],
        denied_mime_types=["video/*"],
        channels=[],
        read_chats=[chat_id],
        write_chats=[chat_id],
        ask_chats=[chat_id],
        max_ops_per_minute=600,
        burst=100,
        concurrent_queue_wait_seconds=2.0,
        log_level="DEBUG",
        log_file=tmp_path / "bridge.log",
        ask_fallback="strict",
        fallback_window_seconds=120,
        poll_buffer_size=256,
        session_path=tmp_path / "test.session",
    )


def make_correlation(tmp_path: Path, config) -> Correlation:
    db_path = tmp_path / ".correlation.db"
    corr = Correlation(str(db_path), config=config)
    corr.open()
    return corr


def fake_message(msg_id: int = 77):
    import datetime
    msg = MagicMock()
    msg.id = msg_id
    msg.date = datetime.datetime.now(datetime.timezone.utc)
    return msg


def fake_reply_msg(reply_to_msg_id: int, text: str = "reply text", sender_id: int = 42):
    import datetime
    msg = MagicMock()
    msg.id = reply_to_msg_id + 1000
    msg.reply_to_msg_id = reply_to_msg_id
    msg.from_id = sender_id
    msg.text = text
    msg.message = text
    msg.date = datetime.datetime.now(datetime.timezone.utc)
    return msg


@pytest.fixture(autouse=True)
def reset_poll():
    poll.reset_state()
    yield
    poll.reset_state()


class TestAskUserWiringViaDispatch:
    """B1 + C3: ask_user wired through dispatch_tool with real Correlation."""

    @pytest.mark.asyncio
    async def test_ask_user_receives_reply_via_correlation(self, tmp_path):
        """Full ask_user flow: send → inject reply → dispatch returns reply_text."""
        from mcp_bridge import server

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        config = make_config(tmp_path, chat_id=5001)
        config.base_dir.mkdir(parents=True, exist_ok=True)

        corr = make_correlation(tmp_path, config)

        mock_client = AsyncMock()
        sent_msg = fake_message(77)
        mock_client.send_message.return_value = sent_msg

        from mcp_bridge.tools.bridge import ask_user as _ask_user

        async def ask_handler(**kw):
            return await _ask_user(
                client=mock_client,
                correlation=corr,
                config=config,
                chat_id=kw["chat_id"],
                text=kw["text"],
                timeout_sec=kw.get("timeout_sec", 2.0),
                target_user_id=kw.get("target_user_id"),
            )

        server._TOOL_REGISTRY["ask_user"] = ask_handler

        try:
            # Schedule a reply injection shortly after the ask is in flight
            async def inject_reply():
                await asyncio.sleep(0.05)
                reply = fake_reply_msg(reply_to_msg_id=77, text="yes, done")
                corr.match_reply(5001, reply)

            asyncio.ensure_future(inject_reply())

            result = await server.dispatch_tool(
                "ask_user",
                {"chat_id": 5001, "text": "are you there?", "timeout_sec": 2.0},
                mock_client,
                config,
                correlation=corr,
            )

            assert "error" not in result, f"Unexpected error: {result}"
            assert result["reply_text"] == "yes, done"
            assert result["ask_token"] is not None
        finally:
            corr.close()
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)

    @pytest.mark.asyncio
    async def test_ask_user_timeout_returns_error(self, tmp_path):
        """ask_user via dispatch_tool returns ASK_TIMEOUT error when no reply arrives."""
        from mcp_bridge import server

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        config = make_config(tmp_path, chat_id=5002)
        config.base_dir.mkdir(parents=True, exist_ok=True)

        corr = make_correlation(tmp_path, config)

        mock_client = AsyncMock()
        mock_client.send_message.return_value = fake_message(88)

        from mcp_bridge.tools.bridge import ask_user as _ask_user

        async def ask_handler(**kw):
            return await _ask_user(
                client=mock_client,
                correlation=corr,
                config=config,
                chat_id=kw["chat_id"],
                text=kw["text"],
                timeout_sec=kw.get("timeout_sec", 0.05),
            )

        server._TOOL_REGISTRY["ask_user"] = ask_handler

        try:
            result = await server.dispatch_tool(
                "ask_user",
                {"chat_id": 5002, "text": "ping?", "timeout_sec": 0.05},
                mock_client,
                config,
                correlation=corr,
            )
            assert "error" in result
            assert result["error"]["code"] == "ASK_TIMEOUT"
        finally:
            corr.close()
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)

    @pytest.mark.asyncio
    async def test_send_failure_no_orphaned_row_via_dispatch(self, tmp_path):
        """I-3 via dispatch: send failure → AskSendFailedError → no pending row in DB."""
        from mcp_bridge import server

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        config = make_config(tmp_path, chat_id=5003)
        config.base_dir.mkdir(parents=True, exist_ok=True)

        corr = make_correlation(tmp_path, config)

        mock_client = AsyncMock()
        mock_client.send_message.side_effect = RuntimeError("network failure")

        from mcp_bridge.tools.bridge import ask_user as _ask_user

        async def ask_handler(**kw):
            return await _ask_user(
                client=mock_client,
                correlation=corr,
                config=config,
                chat_id=kw["chat_id"],
                text=kw["text"],
                timeout_sec=kw.get("timeout_sec", 2.0),
            )

        server._TOOL_REGISTRY["ask_user"] = ask_handler

        try:
            result = await server.dispatch_tool(
                "ask_user",
                {"chat_id": 5003, "text": "hello?", "timeout_sec": 2.0},
                mock_client,
                config,
                correlation=corr,
            )
            # Should get ASK_SEND_FAILED error
            assert "error" in result
            assert result["error"]["code"] == "ASK_SEND_FAILED"

            # No orphaned rows in DB
            row = corr._db.execute("SELECT * FROM pending_asks").fetchone()
            assert row is None, "No row should be present when send fails before insert_pending"
        finally:
            corr.close()
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)


class TestAuditLogEmission:
    """I1: dispatch_tool emits JSON-lines audit log entry per tool call."""

    @pytest.mark.asyncio
    async def test_dispatch_tool_emits_audit_log_entry(self, tmp_path, caplog):
        """Verify dispatch_tool logs a tool_invocation entry via log_tool."""
        from mcp_bridge import server
        from mcp_bridge.tools.poll import poll_chat_since

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        config = make_config(tmp_path, chat_id=6001)
        mock_client = MagicMock()

        # Pre-inject a message so poll returns immediately
        poll.ingest_message(6001, {
            "message_id": 101,
            "text": "audit test",
            "from_id": 1,
            "reply_to_msg_id": None,
            "date": "2026-01-01T00:00:00",
            "has_media": False,
            "media_summary": None,
        })

        async def poll_handler(**kw):
            return await poll_chat_since(
                config=config,
                chat_id=kw["chat_id"],
                since_message_id=kw.get("since_message_id", 0),
                timeout_ms=kw.get("timeout_ms", 100),
                connection_id="audit-test-conn",
                client=None,
            )

        server._TOOL_REGISTRY["poll_chat_since"] = poll_handler

        try:
            with caplog.at_level(logging.INFO, logger="mcp_bridge.tools"):
                result = await server.dispatch_tool(
                    "poll_chat_since",
                    {"chat_id": 6001, "since_message_id": 0, "timeout_ms": 100},
                    mock_client,
                    config,
                )

            assert "error" not in result

            # Find the audit log record
            audit_records = [
                r for r in caplog.records
                if r.name == "mcp_bridge.tools" and "tool_invocation" in r.getMessage()
            ]
            assert len(audit_records) >= 1, "Expected at least one audit log entry"

            rec = audit_records[0]
            assert getattr(rec, "tool", None) == "poll_chat_since"
            assert getattr(rec, "outcome", None) == "ok"
            assert getattr(rec, "pid", None) == os.getpid()
            assert getattr(rec, "duration_ms", None) is not None
        finally:
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)
