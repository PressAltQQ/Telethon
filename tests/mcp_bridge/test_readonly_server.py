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
