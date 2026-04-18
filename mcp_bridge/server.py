"""
MCP stdio server for the Telethon bridge.

Registers tool handlers dispatched by name → callable.
Tools registered this sprint: list_channel_files, download_file,
  send_message (Sprint 4), ask_user (Sprint 4).
Stub registrations for Sprint 5: poll_chat_since.

All errors surface as {"error": {"code": "...", "message": "...", ...}} per spec §5.
Tracebacks are never leaked to the MCP client surface.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from mcp_bridge.errors import BridgeError, InternalError, NotImplementedBridgeError, to_error_response

__log__ = logging.getLogger(__name__)

# Tool registry: tool_name → async callable(client, config, **kwargs) → dict
_TOOL_REGISTRY: Dict[str, Callable] = {}

# Tools that remain stubbed until Sprint 5
_STUB_TOOLS = {"poll_chat_since"}


def register_tool(name: str, handler: Callable) -> None:
    """Register a tool handler by name."""
    _TOOL_REGISTRY[name] = handler


async def _stub_handler(**kwargs) -> dict:
    """Raise NotImplementedBridgeError for not-yet-implemented tools."""
    raise NotImplementedBridgeError("not yet implemented")


def _register_stubs() -> None:
    for name in _STUB_TOOLS:
        _TOOL_REGISTRY[name] = _stub_handler


async def dispatch_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    client,
    config,
    correlation=None,
) -> dict:
    """Dispatch a tool call by name, returning a structured result.

    Never raises — all errors are caught and returned as structured error dicts.
    """
    handler = _TOOL_REGISTRY.get(tool_name)
    if handler is None:
        return {"error": {"code": "INTERNAL", "message": f"Unknown tool: {tool_name!r}"}}

    try:
        if tool_name == "ask_user":
            return await handler(
                client=client, config=config, correlation=correlation, **arguments
            )
        return await handler(client=client, config=config, **arguments)
    except BridgeError as exc:
        __log__.warning("Tool %r returned error: %s", tool_name, exc)
        return to_error_response(exc)
    except Exception:
        __log__.exception("Unexpected error in tool %r", tool_name)
        bridge_exc = InternalError(f"Unexpected error in {tool_name!r}")
        return to_error_response(bridge_exc)


def build_server(client, config, correlation=None):
    """Build and return an MCP FastMCP server with all tools registered.

    Registers the tool list from _TOOL_REGISTRY, including stubs.
    """
    from mcp.server.fastmcp import FastMCP

    _register_stubs()

    mcp = FastMCP("telethon-mcp-bridge")

    # Register all tools dynamically
    for tool_name, handler in _TOOL_REGISTRY.items():
        # Create a closure to capture tool_name and handler
        def make_tool(name, h):
            async def tool_fn(**kwargs):
                result = await dispatch_tool(name, kwargs, client, config, correlation)
                return result
            tool_fn.__name__ = name
            return tool_fn

        mcp.add_tool(make_tool(tool_name, handler), name=tool_name)

    return mcp


async def run_server(client, config, correlation=None) -> None:
    """Run the MCP stdio server until shutdown."""
    from mcp.server.stdio import stdio_server

    _register_stubs()

    from mcp_bridge.tools.bridge import ask_user as _ask_user
    from mcp_bridge.tools.bridge import send_message as _send_message
    from mcp_bridge.tools.downloader import download_file, list_channel_files

    register_tool("list_channel_files", lambda **kw: list_channel_files(
        client, config, kw["channel_id"],
        since=kw.get("since"), limit=kw.get("limit", 100)
    ))
    register_tool("download_file", lambda **kw: download_file(
        client, config, kw["channel_id"], kw["message_id"],
        batch_cursor=kw.get("batch_cursor")
    ))
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
