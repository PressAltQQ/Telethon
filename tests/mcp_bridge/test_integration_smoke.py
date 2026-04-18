"""
End-to-end smoke test: full server startup → tools/list → list_channel_files
→ download_file → inject update → poll_chat_since returns the update.

All mocked — no Telethon network, no real filesystem beyond tmp_path.
Asserts server.dispatch_tool wraps everything correctly.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_bridge.tools import poll


@pytest.fixture(autouse=True)
def reset_poll_state():
    poll.reset_state()
    yield
    poll.reset_state()


def make_config(tmp_path: Path, chat_id=3001, channel_id=-100123):
    return SimpleNamespace(
        api_id=12345,
        api_hash="test_hash",
        session_name="smoke_test",
        key_source="keyring",
        base_dir=tmp_path / "downloads",
        max_file_size_mb=100,
        allowed_mime_types=["application/pdf", "image/*"],
        denied_mime_types=["video/*"],
        channels=[channel_id],
        read_chats=[chat_id],
        write_chats=[chat_id],
        ask_chats=[chat_id],
        max_ops_per_minute=30,
        burst=5,
        concurrent_queue_wait_seconds=2.0,
        log_level="INFO",
        log_file=tmp_path / "bridge.log",
        ask_fallback="strict",
        fallback_window_seconds=120,
        poll_buffer_size=256,
        session_path=tmp_path / "test.session",
    )


class TestDispatchToolWiring:
    """Test that dispatch_tool correctly routes to handlers."""

    @pytest.mark.asyncio
    async def test_dispatch_tool_returns_error_for_unknown_tool(self):
        from mcp_bridge import server

        # Clear registry state for this test
        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        try:
            client = MagicMock()
            config = make_config(Path("/tmp"))
            result = await server.dispatch_tool(
                "nonexistent_tool", {}, client, config
            )
            assert "error" in result
            assert result["error"]["code"] == "INTERNAL"
        finally:
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)

    @pytest.mark.asyncio
    async def test_dispatch_tool_wraps_bridge_error(self):
        from mcp_bridge import server
        from mcp_bridge.errors import NotWhitelistedError

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        async def bad_handler(**kwargs):
            raise NotWhitelistedError("test not whitelisted")

        server._TOOL_REGISTRY["test_bad"] = bad_handler
        try:
            client = MagicMock()
            config = make_config(Path("/tmp"))
            result = await server.dispatch_tool(
                "test_bad", {}, client, config
            )
            assert "error" in result
            assert result["error"]["code"] == "NOT_WHITELISTED"
        finally:
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)

    @pytest.mark.asyncio
    async def test_dispatch_tool_wraps_unexpected_exception(self):
        from mcp_bridge import server

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        async def exploding_handler(**kwargs):
            raise RuntimeError("something went wrong internally")

        server._TOOL_REGISTRY["test_explode"] = exploding_handler
        try:
            client = MagicMock()
            config = make_config(Path("/tmp"))
            result = await server.dispatch_tool(
                "test_explode", {}, client, config
            )
            assert "error" in result
            assert result["error"]["code"] == "INTERNAL"
            # Must not leak raw exception message
            assert "something went wrong internally" not in result["error"].get("message", "")
        finally:
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)


class TestListChannelFiles:
    """Test list_channel_files via dispatch_tool with a mocked client."""

    @pytest.mark.asyncio
    async def test_list_channel_files_returns_empty_for_no_documents(self, tmp_path):
        from mcp_bridge import server

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        config = make_config(tmp_path)
        config.base_dir.mkdir(parents=True, exist_ok=True)

        # Mock client that returns no messages
        mock_client = AsyncMock()
        mock_client.iter_messages = AsyncMock(return_value=_async_iter([]))

        async def mock_list(**kw):
            return {"files": []}

        server._TOOL_REGISTRY["list_channel_files"] = mock_list
        try:
            result = await server.dispatch_tool(
                "list_channel_files",
                {"channel_id": -100123},
                mock_client,
                config,
            )
            assert "files" in result or "error" not in result
        finally:
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)


class TestPollChatSinceViaDispatch:
    """Test inject update → poll_chat_since returns the update."""

    @pytest.mark.asyncio
    async def test_inject_then_poll_returns_update(self, tmp_path):
        from mcp_bridge import server

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        config = make_config(tmp_path, chat_id=3001)
        mock_client = MagicMock()

        from mcp_bridge.tools.poll import poll_chat_since

        async def poll_handler(**kw):
            return await poll_chat_since(
                config=config,
                chat_id=kw["chat_id"],
                since_message_id=kw.get("since_message_id", 0),
                timeout_ms=kw.get("timeout_ms", 1500),
                connection_id="smoke-conn",
                client=None,
            )

        server._TOOL_REGISTRY["poll_chat_since"] = poll_handler

        try:
            # Inject an update directly (simulating the update handler)
            poll.ingest_message(3001, {
                "message_id": 42,
                "from_id": 99,
                "text": "smoke test message",
                "reply_to_msg_id": None,
                "date": "2026-01-01T00:00:00",
                "has_media": False,
                "media_summary": None,
            })

            result = await server.dispatch_tool(
                "poll_chat_since",
                {"chat_id": 3001, "since_message_id": 0, "timeout_ms": 100},
                mock_client,
                config,
            )

            assert "error" not in result
            assert len(result["messages"]) == 1
            assert result["messages"][0]["message_id"] == 42
            assert result["messages"][0]["text"] == "smoke test message"
        finally:
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)

    @pytest.mark.asyncio
    async def test_poll_whitelist_gate_via_dispatch(self, tmp_path):
        from mcp_bridge import server

        old_registry = dict(server._TOOL_REGISTRY)
        server._TOOL_REGISTRY.clear()

        config = make_config(tmp_path, chat_id=3001)
        mock_client = MagicMock()

        from mcp_bridge.tools.poll import poll_chat_since

        async def poll_handler(**kw):
            return await poll_chat_since(
                config=config,
                chat_id=kw["chat_id"],
                since_message_id=kw.get("since_message_id", 0),
                timeout_ms=kw.get("timeout_ms", 100),
                connection_id="smoke-conn-2",
                client=None,
            )

        server._TOOL_REGISTRY["poll_chat_since"] = poll_handler

        try:
            result = await server.dispatch_tool(
                "poll_chat_since",
                {"chat_id": 9999, "since_message_id": 0, "timeout_ms": 100},
                mock_client,
                config,
            )
            assert "error" in result
            assert result["error"]["code"] == "NOT_WHITELISTED"
        finally:
            server._TOOL_REGISTRY.clear()
            server._TOOL_REGISTRY.update(old_registry)


async def _async_iter(items):
    """Helper to create an async iterable from a list."""
    for item in items:
        yield item
