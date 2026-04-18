"""
TelegramClient lifecycle management with single-instance fcntl lock.

Unix-only (fcntl). Windows is explicitly rejected at import time.
"""
from __future__ import annotations

import logging
import os
import sys

if sys.platform == "win32":
    raise ImportError("mcp_bridge is Unix-only")

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


def _on_update(update) -> None:
    """Update handler stub. Sprint 4 fills in correlation hook, Sprint 5 ring buffer."""
    pass


async def start(config) -> None:
    """Start the TelegramClient and acquire the single-instance lock.

    Raises SessionLockedError if another instance holds the lock.
    """
    global _client, _lock_fd, _lock_path

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

    # Build session and client
    session = EncryptedSQLiteSession(str(session_path), keystore_name=config.session_name)
    _client = TelegramClient(session, config.api_id, config.api_hash)
    _client.add_event_handler(_on_update)

    await _client.connect()
    __log__.info("TelegramClient connected, session=%s", config.session_name)


async def stop() -> None:
    """Disconnect the TelegramClient and release the single-instance lock."""
    global _client, _lock_fd, _lock_path

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


def client() -> Optional[TelegramClient]:
    """Return the current TelegramClient instance."""
    return _client
