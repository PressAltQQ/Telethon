"""
MCP stdio server for the Telethon bridge.

Registers tool handlers dispatched by name → callable.
Tools registered: list_channel_files, download_file,
  send_message, ask_user, poll_chat_since.

All errors surface as {"error": {"code": "...", "message": "...", ...}} per spec §5.
Tracebacks are never leaked to the MCP client surface.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable, Dict

from mcp_bridge.errors import BridgeError, InternalError, to_error_response
from mcp_bridge.logging_setup import hash_chat_id, log_tool
from mcp_bridge.tools import poll as _poll_module

__log__ = logging.getLogger(__name__)

# Module-level connection_id: single value per process lifetime (one daemon = one connection)
_CONNECTION_ID: str = str(uuid.uuid4())
_poll_module.set_connection_id(_CONNECTION_ID)

# Tool registry: tool_name → async callable(client, config, **kwargs) → dict
_TOOL_REGISTRY: Dict[str, Callable] = {}

# Static key for chat_id hashing when no session root_key is available.
# In production, replace with the session root_key if accessible.
_HASH_KEY_FALLBACK: bytes = b"\x00" * 32


def _get_root_key() -> bytes:
    """Return the root_key for hash_chat_id, falling back to a zero key.

    Tries to load from env vars; returns zero bytes if unavailable.
    This ensures hash_chat_id is always called with *some* key — the
    audit log entry is still emitted even when the session root_key is
    not directly accessible from server.py.
    """
    import base64
    key_b64 = os.environ.get("TELETHON_SESSION_KEY")
    if key_b64:
        try:
            key = base64.b64decode(key_b64)
            if len(key) == 32:
                return key
        except Exception:
            pass
    return _HASH_KEY_FALLBACK


def _extract_chat_identifier(tool_name: str, arguments: Dict[str, Any]) -> int | None:
    """Extract the chat/channel identifier from tool arguments for audit hashing."""
    # Most tools use chat_id; downloader tools use channel_id
    return arguments.get("chat_id") or arguments.get("channel_id")


def register_tool(name: str, handler: Callable) -> None:
    """Register a tool handler by name."""
    _TOOL_REGISTRY[name] = handler


async def dispatch_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    client,
    config,
    correlation=None,
) -> dict:
    """Dispatch a tool call by name, returning a structured result.

    Wraps every call with log_tool + hash_chat_id for the JSON-lines audit log.
    Never raises — all errors are caught and returned as structured error dicts.
    """
    handler = _TOOL_REGISTRY.get(tool_name)
    if handler is None:
        return {"error": {"code": "INTERNAL", "message": f"Unknown tool: {tool_name!r}"}}

    chat_identifier = _extract_chat_identifier(tool_name, arguments)
    root_key = _get_root_key()
    chat_id_hashed = hash_chat_id(chat_identifier, root_key) if chat_identifier is not None else None

    with log_tool(tool_name, chat_id_hashed, os.getpid()) as ctx:
        try:
            if tool_name == "ask_user":
                result = await handler(
                    client=client, config=config, correlation=correlation, **arguments
                )
            else:
                result = await handler(client=client, config=config, **arguments)
            ctx.outcome = "ok"
            return result
        except BridgeError as exc:
            __log__.warning("Tool %r returned error: %s", tool_name, exc)
            ctx.outcome = f"error:{exc.CODE}"
            return to_error_response(exc)
        except Exception:
            __log__.exception("Unexpected error in tool %r", tool_name)
            bridge_exc = InternalError(f"Unexpected error in {tool_name!r}")
            ctx.outcome = f"error:{bridge_exc.CODE}"
            return to_error_response(bridge_exc)


async def run_server(client, config, correlation=None) -> None:
    """Run the MCP stdio server until shutdown."""
    from mcp.server.stdio import stdio_server

    readonly = os.environ.get("MCP_READONLY") == "1"

    from mcp_bridge.tools.downloader import download_file, list_channel_files
    from mcp_bridge.tools.poll import poll_chat_since as _poll_chat_since
    if not readonly:
        from mcp_bridge.tools.bridge import ask_user as _ask_user
        from mcp_bridge.tools.bridge import send_message as _send_message

    register_tool("list_channel_files", lambda **kw: list_channel_files(
        client, config, kw["channel_id"],
        since=kw.get("since"), limit=kw.get("limit", 100)
    ))
    register_tool("download_file", lambda **kw: download_file(
        client, config, kw["channel_id"], kw["message_id"],
        batch_cursor=kw.get("batch_cursor")
    ))

    if not readonly:
        register_tool("send_message", lambda **kw: _send_message(
            client, config, kw["chat_id"], kw["text"]
        ))

        async def _ask_user_handler(**kw):
            return await _ask_user(
                client=client,
                correlation=correlation,
                config=config,
                chat_id=kw["chat_id"],
                text=kw["text"],
                timeout_sec=kw.get("timeout_sec", 1800),
                target_user_id=kw.get("target_user_id"),
            )

        register_tool("ask_user", _ask_user_handler)
    else:
        __log__.info("MCP_READONLY=1 — skipping send_message/ask_user registration")

    async def _poll_handler(**kw):
        return await _poll_chat_since(
            config=config,
            chat_id=kw["chat_id"],
            since_message_id=kw["since_message_id"],
            timeout_ms=kw.get("timeout_ms", 1500),
            connection_id=_CONNECTION_ID,
            client=client,
        )

    register_tool("poll_chat_since", _poll_handler)

    # Build lowlevel server for stdio
    from mcp import types
    from mcp.server import Server

    server = Server("telethon-mcp-bridge")

    @server.list_tools()
    async def handle_list_tools():
        tools = []
        for name in _TOOL_REGISTRY:
            tools.append(types.Tool(
                name=name,
                description=f"Tool: {name}",
                inputSchema={"type": "object", "properties": {}},
            ))
        return tools

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        import json
        result = await dispatch_tool(name, arguments or {}, client, config, correlation)
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
