"""
Realtime long-polling tool for whitelisted chats.

Per-chat ring buffer of recent messages, woken by asyncio.Condition
when new messages arrive via ingest_message().

Spec §2.6 — primary realtime delivery API (polling, not push).
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time
import weakref
from typing import Any

from mcp_bridge.errors import NotWhitelistedError, PollAlreadyActiveError, PollCursorLostError

__log__ = logging.getLogger(__name__)

# Weak-reference set to keep pending notify tasks alive until they complete,
# preventing GC from collecting them before the coroutine finishes.
_NOTIFY_TASKS: weakref.WeakSet = weakref.WeakSet()

# Per-chat ring buffers: chat_id → deque of message metadata dicts
_BUFFERS: dict[int, collections.deque] = {}

# Per-chat asyncio.Condition (lazy creation) for waking long-poll waiters
_CONDITIONS: dict[int, asyncio.Condition] = {}

# Active polls: set of (connection_id, chat_id) tuples
_ACTIVE_POLLS: set[tuple] = set()

# Module-level connection_id set at server startup
_CONNECTION_ID: str = "default"

_DEFAULT_TIMEOUT_MS = 1500
_MAX_TIMEOUT_MS = 10_000

# Documented metadata fields only — no raw file bytes
_MSG_FIELDS = ("message_id", "from_id", "text", "reply_to_msg_id", "date", "has_media", "media_summary")

# Module-level buffer size; set via set_buffer_size() at startup from config.
# Existing buffers (deques already created) keep their old maxlen — they are
# NOT resized retroactively.  This is a startup-time configuration, not a
# live-resize API.
_BUFFER_SIZE: int = 256


def set_buffer_size(size: int) -> None:
    """Set the module-level default buffer size for newly created deques.

    Call once at startup after config is loaded, before any ingest_message()
    calls that would create buffers.  Already-existing deques are NOT resized.
    """
    global _BUFFER_SIZE
    _BUFFER_SIZE = size


def set_connection_id(connection_id: str) -> None:
    """Set the module-level connection_id for this daemon process."""
    global _CONNECTION_ID
    _CONNECTION_ID = connection_id


def _get_buffer(chat_id: int, buffer_size: int) -> collections.deque:
    """Return or create the ring buffer for a chat, bounded by buffer_size."""
    if chat_id not in _BUFFERS:
        _BUFFERS[chat_id] = collections.deque(maxlen=buffer_size)
    return _BUFFERS[chat_id]


def _get_condition(chat_id: int) -> asyncio.Condition:
    """Return or create the asyncio.Condition for a chat."""
    if chat_id not in _CONDITIONS:
        _CONDITIONS[chat_id] = asyncio.Condition()
    return _CONDITIONS[chat_id]


def _filter_metadata(msg: dict) -> dict:
    """Return only the documented metadata fields from a message dict."""
    result = {}
    for field in _MSG_FIELDS:
        if field in msg:
            result[field] = msg[field]
    return result


def ingest_message(chat_id: int, msg_metadata: dict) -> None:
    """Append a message to the ring buffer and wake any waiting poll.

    Called from the update handler after whitelist filtering.
    Message metadata fields: message_id, from_id, text, reply_to_msg_id,
    date, has_media, media_summary. Never includes raw file bytes.

    Buffer size is controlled by the module-level _BUFFER_SIZE variable,
    set at startup via set_buffer_size(config.poll_buffer_size).
    """
    buf = _BUFFERS.get(chat_id)
    if buf is None:
        _BUFFERS[chat_id] = collections.deque(maxlen=_BUFFER_SIZE)
        buf = _BUFFERS[chat_id]

    # Only store documented fields
    safe_meta = _filter_metadata(msg_metadata)
    buf.append(safe_meta)
    __log__.debug("ingest_message: chat_id=%s message_id=%s", chat_id, msg_metadata.get("message_id"))

    # Wake any waiting condition (must be done in the running event loop)
    cond = _CONDITIONS.get(chat_id)
    if cond is not None:
        try:
            # get_running_loop() raises RuntimeError if no loop is running.
            # In that case we skip the notification (pure-sync test context);
            # the poll will pick up messages on the next _collect_new() call.
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(_notify_condition, cond)
        except RuntimeError:
            # No event loop running — notification skipped (sync/test context)
            pass


def _notify_condition(cond: asyncio.Condition) -> None:
    """Schedule a notify_all on the condition from within the event loop.

    Creates a named task and registers a done_callback so that any exception
    is logged at WARNING rather than silently swallowed.
    """
    task = asyncio.ensure_future(_async_notify(cond))
    task.set_name("poll_notify_condition")
    # Keep a strong reference until the task completes (prevents GC).
    _NOTIFY_TASKS.add(task)
    task.add_done_callback(_on_notify_done)


def _on_notify_done(task: asyncio.Task) -> None:
    """Log any exception raised by a notify task."""
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None:
        __log__.warning("poll notify task raised exception: %r", exc)


async def _async_notify(cond: asyncio.Condition) -> None:
    async with cond:
        cond.notify_all()


def reset_state() -> None:
    """Clear all module-level state. For testing only."""
    _BUFFERS.clear()
    _CONDITIONS.clear()
    _ACTIVE_POLLS.clear()


async def poll_chat_since(
    config,
    chat_id: int,
    since_message_id: int,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    connection_id: str | None = None,
    client=None,  # accepted but unused (routing compatibility)
) -> dict[str, Any]:
    """Long-poll for new messages in a whitelisted chat.

    Returns immediately if messages newer than since_message_id are buffered.
    Otherwise waits up to timeout_ms milliseconds, then returns empty list.

    Raises:
        NotWhitelistedError: chat_id not in config.read_chats.
        PollAlreadyActiveError: another poll for (connection_id, chat_id) is active.
        PollCursorLostError: since_message_id pre-dates the ring buffer (gap detected).
            Not raised when since_message_id=0 (bootstrap — fresh client).

    Returns:
        {
            "messages": [<message_metadata>, ...],
            "next_since_message_id": <int>,
            "server_wait_ms": <float>,
        }

    The returned messages list is sorted by message_id ascending so that
    next_since_message_id is always the highest ID seen.
    """
    if connection_id is None:
        connection_id = _CONNECTION_ID

    # Whitelist gate
    read_chats = list(getattr(config, "read_chats", []))
    if chat_id not in read_chats:
        raise NotWhitelistedError(
            f"chat_id {chat_id} is not in read_chats whitelist"
        )

    # Concurrency guard
    poll_key = (connection_id, chat_id)
    if poll_key in _ACTIVE_POLLS:
        raise PollAlreadyActiveError(
            f"A poll for chat_id={chat_id} connection={connection_id!r} is already active"
        )

    # Clamp timeout
    timeout_ms = max(0, min(timeout_ms, _MAX_TIMEOUT_MS))
    timeout_sec = timeout_ms / 1000.0

    buffer_size = getattr(config, "poll_buffer_size", _BUFFER_SIZE)

    _ACTIVE_POLLS.add(poll_key)
    t_start = time.monotonic()
    try:
        buf = _get_buffer(chat_id, buffer_size)
        cond = _get_condition(chat_id)

        # Cursor-lost check: if since_message_id is non-zero and falls before
        # the range we can guarantee (oldest buffered minus 1), messages in the
        # interval (since_message_id, oldest_id) have been evicted.
        # since_message_id=0 is the bootstrap/fresh-client value — skip check.
        if buf and since_message_id != 0:
            oldest_id = min(m.get("message_id", 0) for m in buf)
            if since_message_id < oldest_id - 1:
                raise PollCursorLostError(
                    f"cursor {since_message_id} behind oldest buffered {oldest_id}"
                )

        # Collect messages newer than since_message_id from the buffer
        def _collect_new() -> list[dict]:
            return [m for m in buf if m.get("message_id", 0) > since_message_id]

        new_msgs = _collect_new()
        if new_msgs:
            elapsed_ms = (time.monotonic() - t_start) * 1000
            sorted_msgs = sorted(new_msgs, key=lambda m: m["message_id"])
            return {
                "messages": sorted_msgs,
                "next_since_message_id": sorted_msgs[-1]["message_id"],
                "server_wait_ms": elapsed_ms,
            }

        # Long-poll wait
        try:
            async with cond:
                await asyncio.wait_for(
                    cond.wait_for(lambda: bool(_collect_new())),
                    timeout=timeout_sec,
                )
        except asyncio.TimeoutError:
            pass

        new_msgs = _collect_new()
        elapsed_ms = (time.monotonic() - t_start) * 1000
        if new_msgs:
            sorted_msgs = sorted(new_msgs, key=lambda m: m["message_id"])
            return {
                "messages": sorted_msgs,
                "next_since_message_id": sorted_msgs[-1]["message_id"],
                "server_wait_ms": elapsed_ms,
            }
        else:
            return {
                "messages": [],
                "next_since_message_id": since_message_id,
                "server_wait_ms": elapsed_ms,
            }

    finally:
        _ACTIVE_POLLS.discard(poll_key)
