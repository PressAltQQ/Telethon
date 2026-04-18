"""
Logging configuration for the MCP bridge.

- JSON lines formatter to file
- WARN/ERROR to stderr
- Third-party logger gating (mcp, telethon.network, telethon.extensions)
- HMAC-SHA-256 chat_id hashing
- ToolInvocationLogger context manager
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Generator

_GATED_LOGGERS = [
    "mcp",
    "telethon.network",
    "telethon.extensions",
]


_STRUCTURED_EXTRA_FIELDS = {"tool", "chat_id_hashed", "pid", "outcome", "duration_ms"}


class _JsonLinesFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            # Include exception type and message, but NOT traceback
            exc_type, exc_val, _ = record.exc_info
            data["exc_type"] = exc_type.__name__ if exc_type else None
            data["exc_message"] = str(exc_val)
        # Merge whitelisted structured extra fields from the log record
        for field in _STRUCTURED_EXTRA_FIELDS:
            if field in record.__dict__:
                data[field] = record.__dict__[field]
        return json.dumps(data)


def setup_logging(config) -> None:
    """Configure logging based on config.

    - JSON lines handler to log file (log_level from config)
    - WARNING and above to stderr
    - Third-party loggers gated to WARNING unless TELETHON_MCP_DEBUG_PROTOCOL=1
    """
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    debug_protocol = os.environ.get("TELETHON_MCP_DEBUG_PROTOCOL") == "1"

    # Root logger
    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove existing handlers
    root.handlers.clear()

    # File handler (JSON lines)
    try:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(_JsonLinesFormatter())
        root.addHandler(file_handler)
    except OSError as exc:
        # Fall back to stderr if file isn't writable
        logging.warning("Cannot open log file %s: %s", config.log_file, exc)

    # Stderr handler (WARNING and above)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(stderr_handler)

    # Gate third-party loggers
    gate_level = logging.DEBUG if debug_protocol else logging.WARNING
    for logger_name in _GATED_LOGGERS:
        lg = logging.getLogger(logger_name)
        lg.setLevel(gate_level)
        lg.propagate = True


def hash_chat_id(chat_id: int, root_key: bytes) -> str:
    """HMAC-SHA-256 truncated to 8 bytes (16 hex chars).

    Keyed uniformly for all chat types (private, group, channel).
    Caller supplies the key; when called from the bridge it should be the session
    root_key (Sprint 4/5 will wire this; Sprint 3 tests use a static key).
    """
    h = hmac.new(root_key, str(chat_id).encode(), hashlib.sha256)
    return h.digest()[:8].hex()


class _ToolCtx:
    """Context object for ToolInvocationLogger."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.outcome: str = "unknown"
        self._start: float = 0.0


@contextmanager
def log_tool(
    tool_name: str,
    chat_id_hashed: str | None,
    pid: int,
) -> Generator[_ToolCtx, None, None]:
    """Context manager that logs a tool invocation with timing and outcome.

    Usage:
        with log_tool('download_file', hash_chat_id(chat_id, key), os.getpid()) as ctx:
            ...
            ctx.outcome = 'ok'
    """
    logger = logging.getLogger("mcp_bridge.tools")
    ctx = _ToolCtx(tool_name)
    ctx._start = time.monotonic()

    try:
        yield ctx
    finally:
        duration_ms = (time.monotonic() - ctx._start) * 1000
        logger.info(
            "tool_invocation",
            extra={
                "tool": tool_name,
                "chat_id_hashed": chat_id_hashed,
                "pid": pid,
                "outcome": ctx.outcome,
                "duration_ms": round(duration_ms, 2),
            },
        )
