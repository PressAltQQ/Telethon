"""
TelegramClient lifecycle management with single-instance fcntl lock.

Unix-only (fcntl). Windows is explicitly rejected at import time.

Sprint 4 additions:
  - Accepts an optional ``correlation`` (Correlation instance) attribute.
  - ``_on_update`` filters by read_chats ∪ ask_chats and calls correlation.match_reply.
  - ``start`` calls correlation.recover_on_startup and logs the banner.
  - ``start`` starts the janitor task; ``stop`` cancels it.
"""
from __future__ import annotations

import logging
import os
import sys

if sys.platform == "win32":
    raise ImportError("mcp_bridge is Unix-only")

import asyncio
import fcntl
from pathlib import Path
from typing import Optional

from mcp_bridge.errors import SessionLockedError
from mcp_bridge.session.encrypted_sqlite import EncryptedSQLiteSession
from telethon import TelegramClient

__log__ = logging.getLogger(__name__)

_client: Optional[object] = None
_lock_fd: Optional[int] = None
_lock_path: Optional[Path] = None
_correlation = None  # set by start() when correlation is provided
_config = None
_janitor_task: Optional[asyncio.Task] = None


def _extract_chat_id(update) -> Optional[int]:
    """Extract chat_id from a Telegram update object.

    Handles UpdateNewMessage / UpdateNewChannelMessage and generic
    message-carrying updates.
    """
    msg = getattr(update, "message", None)
    if msg is None:
        return None
    peer = getattr(msg, "peer_id", None) or getattr(msg, "to_id", None)
    if peer is None:
        return None
    # PeerChat, PeerChannel, PeerUser all carry some numeric ID
    chat_id = (
        getattr(peer, "channel_id", None)
        or getattr(peer, "chat_id", None)
        or getattr(peer, "user_id", None)
    )
    return chat_id


def _on_update(update) -> None:
    """Update handler: whitelist-filter and feed messages to correlation."""
    if _correlation is None or _config is None:
        return

    chat_id = _extract_chat_id(update)
    if chat_id is None:
        return

    allowed_chats = set(_config.read_chats) | set(_config.ask_chats)
    if chat_id not in allowed_chats:
        return

    msg = getattr(update, "message", None)
    if msg is None:
        return

    try:
        _correlation.match_reply(chat_id, msg)
    except Exception:
        __log__.exception("Error in correlation.match_reply for chat_id=%s", chat_id)


async def start(config, correlation=None) -> None:
    """Start the TelegramClient and acquire the single-instance lock.

    Raises SessionLockedError if another instance holds the lock.
    Calls correlation.recover_on_startup and starts the janitor task if
    correlation is provided.
    """
    global _client, _lock_fd, _lock_path, _correlation, _config, _janitor_task

    _config = config
    _correlation = correlation

    session_path = config.session_path or (
        config.base_dir / f"{config.session_name}.session"
    )

    lock_path = Path(str(session_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Create lockfile 0o600 if absent
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Read holder PID from lockfile
        holder_pid: Optional[int] = None
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            contents = os.read(fd, 32).decode().strip()
            if contents.isdigit():
                holder_pid = int(contents)
        except OSError:
            pass
        os.close(fd)
        raise SessionLockedError(
            f"Another instance is running (PID {holder_pid})",
            holder_pid=holder_pid,
        )

    # Write our PID to the lockfile
    pid_bytes = str(os.getpid()).encode()
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, pid_bytes)

    _lock_fd = fd
    _lock_path = lock_path

    # Startup recovery
    if correlation is not None:
        correlation.open()
        summary = correlation.recover_on_startup()
        __log__.info(
            "Startup recovery complete: timed_out=%d orphaned=%d",
            summary.get("timed_out_count", 0),
            summary.get("orphaned_count", 0),
        )
        _janitor_task = asyncio.ensure_future(correlation.janitor_loop())

    # Build session and client
    session = EncryptedSQLiteSession(str(session_path), keystore_name=config.session_name)
    _client = TelegramClient(session, config.api_id, config.api_hash)
    _client.add_event_handler(_on_update)

    await _client.connect()
    __log__.info("TelegramClient connected, session=%s", config.session_name)


async def stop() -> None:
    """Disconnect the TelegramClient and release the single-instance lock."""
    global _client, _lock_fd, _lock_path, _correlation, _config, _janitor_task

    if _janitor_task is not None:
        _janitor_task.cancel()
        try:
            await _janitor_task
        except asyncio.CancelledError:
            pass
        _janitor_task = None

    if _client is not None:
        try:
            await _client.disconnect()
            __log__.info("TelegramClient disconnected")
        except Exception as exc:
            __log__.warning("Error during disconnect: %s", exc)
        _client = None

    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            os.close(_lock_fd)
        except OSError as exc:
            __log__.warning("Error releasing lock: %s", exc)
        _lock_fd = None
        _lock_path = None

    _correlation = None
    _config = None


def client() -> Optional[TelegramClient]:
    """Return the current TelegramClient instance."""
    return _client
