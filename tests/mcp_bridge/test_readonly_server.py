"""Tests for read-only mode wiring inside server.run_server."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mcp_bridge.server as server_mod


def _stub_config():
    return SimpleNamespace(max_ops_per_minute=60, burst=5)


def _patched_run(monkeypatch):
    """Run run_server() far enough to register tools, then bail out.

    We stub stdio_server so the loop never starts; we capture the registry by
    inspecting server_mod._TOOL_REGISTRY after the registrations execute.
    """
    server_mod._TOOL_REGISTRY.clear()

    class _StubStdioCM:
        async def __aenter__(self):
            # Raise to break out of run_server before it actually serves.
            raise RuntimeError("__stop_after_register__")
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "mcp.server.stdio.stdio_server", lambda: _StubStdioCM(),
    )


@pytest.mark.asyncio
async def test_full_mode_registers_send_message_and_ask_user(monkeypatch):
    monkeypatch.delenv("MCP_READONLY", raising=False)
    _patched_run(monkeypatch)
    client = MagicMock()
    config = _stub_config()
    with pytest.raises(RuntimeError, match="__stop_after_register__"):
        await server_mod.run_server(client, config, correlation=MagicMock())
    assert "send_message" in server_mod._TOOL_REGISTRY
    assert "ask_user" in server_mod._TOOL_REGISTRY
    assert "poll_chat_since" in server_mod._TOOL_REGISTRY
    assert "download_file" in server_mod._TOOL_REGISTRY


@pytest.mark.asyncio
async def test_readonly_mode_omits_send_message_and_ask_user(monkeypatch):
    monkeypatch.setenv("MCP_READONLY", "1")
    _patched_run(monkeypatch)
    client = MagicMock()
    config = _stub_config()
    with pytest.raises(RuntimeError, match="__stop_after_register__"):
        await server_mod.run_server(client, config, correlation=MagicMock())
    assert "send_message" not in server_mod._TOOL_REGISTRY
    assert "ask_user" not in server_mod._TOOL_REGISTRY
    assert "poll_chat_since" in server_mod._TOOL_REGISTRY
    assert "download_file" in server_mod._TOOL_REGISTRY


class TestDownloadRateLimit:
    @pytest.mark.asyncio
    async def test_readonly_acquires_token_before_download(self, monkeypatch):
        """In read-only mode, download_file must call rate_limiter.acquire()."""
        monkeypatch.setenv("MCP_READONLY", "1")

        from mcp_bridge import rate_limit

        rate_limit.reset_rate_limiter()
        bucket = MagicMock()
        bucket.acquire = AsyncMock()
        monkeypatch.setattr(
            "mcp_bridge.rate_limit.get_rate_limiter", lambda config: bucket
        )

        from mcp_bridge.tools import downloader

        # Force whitelist + early-exit path: not_whitelisted is the cheapest
        # branch to verify the acquire() was called BEFORE the whitelist check.
        config = SimpleNamespace(
            channels=[],
            read_chats=[],
            write_chats=[],
            ask_chats=[],
            max_ops_per_minute=60,
            burst=5,
        )

        # We expect NotWhitelistedError, but acquire() must have been called first.
        from mcp_bridge.errors import NotWhitelistedError
        with pytest.raises(NotWhitelistedError):
            await downloader.download_file(MagicMock(), config, channel_id=999, message_id=1)
        bucket.acquire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_full_mode_does_not_acquire_token_for_download(self, monkeypatch):
        monkeypatch.delenv("MCP_READONLY", raising=False)

        from mcp_bridge import rate_limit
        rate_limit.reset_rate_limiter()
        bucket = MagicMock()
        bucket.acquire = AsyncMock()
        monkeypatch.setattr(
            "mcp_bridge.rate_limit.get_rate_limiter", lambda config: bucket
        )

        from mcp_bridge.tools import downloader
        config = SimpleNamespace(
            channels=[],
            read_chats=[],
            write_chats=[],
            ask_chats=[],
            max_ops_per_minute=60,
            burst=5,
        )
        from mcp_bridge.errors import NotWhitelistedError
        with pytest.raises(NotWhitelistedError):
            await downloader.download_file(MagicMock(), config, channel_id=999, message_id=1)
        bucket.acquire.assert_not_called()
