"""
Downloader tool: list_channel_files and download_file.

Owns a SQLite index at {config.base_dir}/.downloads.db.
Schema: downloads(channel_id, message_id, size, sha256, local_path, downloaded_at)
  PRIMARY KEY (channel_id, message_id).

SC1: Never include auth_key or session-internal bytes in returned dicts.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp_bridge.errors import (
    DownloadBusyError,
    FileTooLargeError,
    MessageNotFoundError,
    NotWhitelistedError,
    UnsupportedMimeError,
)
from mcp_bridge.filters import (
    channel_slug,
    check_file,
    is_whitelisted,
    safe_name,
)

__log__ = logging.getLogger(__name__)

# Module-level concurrency cap: one download at a time
_download_semaphore = asyncio.Semaphore(1)

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    channel_id   INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    size         INTEGER,
    sha256       TEXT,
    local_path   TEXT,
    downloaded_at REAL,
    PRIMARY KEY (channel_id, message_id)
);
"""


def _get_db(config) -> sqlite3.Connection:
    """Return a SQLite connection to the downloads index."""
    db_path = Path(config.base_dir) / ".downloads.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(_DB_SCHEMA)
    conn.commit()
    return conn


def _sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _get_index_row(conn: sqlite3.Connection, channel_id: int, message_id: int) -> Optional[dict]:
    """Return the index row for (channel_id, message_id) or None."""
    row = conn.execute(
        "SELECT channel_id, message_id, size, sha256, local_path, downloaded_at "
        "FROM downloads WHERE channel_id=? AND message_id=?",
        (channel_id, message_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "channel_id": row[0],
        "message_id": row[1],
        "size": row[2],
        "sha256": row[3],
        "local_path": row[4],
        "downloaded_at": row[5],
    }


def _upsert_index_row(
    conn: sqlite3.Connection,
    channel_id: int,
    message_id: int,
    size: int,
    sha256: str,
    local_path: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO downloads
           (channel_id, message_id, size, sha256, local_path, downloaded_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (channel_id, message_id, size, sha256, local_path, time.time()),
    )
    conn.commit()


async def list_channel_files(
    client,
    config,
    channel_id: int,
    since: Optional[int] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List files in a channel that pass size/MIME filters.

    Returns [{message_id, file_name, size, mime_type, date}].
    Whitelist gate: channel_id must be in config.channels.
    Max limit: 1000.
    """
    if not is_whitelisted(channel_id, "channels", config):
        raise NotWhitelistedError(
            f"Channel {channel_id} is not in the channels whitelist"
        )

    limit = min(limit, 1000)
    results = []

    async for msg in client.iter_messages(channel_id, limit=limit):
        # Re-check whitelist on each iteration (spec §2.5 abort gracefully)
        if not is_whitelisted(channel_id, "channels", config):
            break

        if since is not None and msg.id <= since:
            break

        if msg.document is None:
            continue

        doc = msg.document
        size = doc.size if hasattr(doc, "size") else 0
        mime_type = doc.mime_type if hasattr(doc, "mime_type") else "application/octet-stream"
        file_name = "unknown"

        # Extract filename from attributes
        if hasattr(doc, "attributes"):
            for attr in doc.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    file_name = attr.file_name
                    break

        try:
            check_file(size, mime_type, config)
        except (FileTooLargeError, UnsupportedMimeError):
            continue

        results.append({
            "message_id": msg.id,
            "file_name": file_name,
            "size": size,
            "mime_type": mime_type,
            "date": msg.date.isoformat() if msg.date else None,
        })

    return results


async def download_file(
    client,
    config,
    channel_id: int,
    message_id: int,
    batch_cursor: Optional[dict] = None,
) -> Dict[str, Any]:
    """Download a file from a channel message.

    - Whitelist gate: channel_id must be in config.channels.
    - Idempotent: if index row exists and sha256 matches, returns already_downloaded=True.
    - Concurrency cap: one download at a time (semaphore, queue wait bounded by
      config.concurrent_queue_wait_seconds).
    - SC1: Never includes auth_key or session bytes in returned dict.

    Returns {local_path, already_downloaded, size?, sha256?}.
    """
    if os.environ.get("MCP_READONLY") == "1":
        from mcp_bridge.rate_limit import get_rate_limiter
        await get_rate_limiter(config).acquire()

    if not is_whitelisted(channel_id, "channels", config):
        extras: dict[str, Any] = {}
        if batch_cursor is not None:
            extras["already_downloaded_count"] = batch_cursor.get("already_downloaded_count", 0)
            extras["pending_count"] = batch_cursor.get("pending_count", 0)
        raise NotWhitelistedError(
            f"Channel {channel_id} is not in the channels whitelist",
            already_downloaded_count=extras.get("already_downloaded_count"),
            pending_count=extras.get("pending_count"),
        )

    with contextlib.closing(_get_db(config)) as conn:
        # Check index for existing download
        existing = _get_index_row(conn, channel_id, message_id)
        if existing is not None:
            local_path = Path(existing["local_path"])
            if local_path.exists():
                # Verify sha256 still matches
                try:
                    current_sha = _sha256_file(local_path)
                    if current_sha == existing["sha256"]:
                        return {
                            "local_path": str(local_path),
                            "already_downloaded": True,
                            "size": existing["size"],
                            "sha256": existing["sha256"],
                        }
                except OSError:
                    pass  # File may have been modified, re-download

        # Acquire download semaphore with bounded wait
        wait_seconds = config.concurrent_queue_wait_seconds
        try:
            await asyncio.wait_for(
                _download_semaphore.acquire(),
                timeout=wait_seconds,
            )
        except asyncio.TimeoutError:
            raise DownloadBusyError(
                f"Download queue wait exceeded {wait_seconds}s — try again later",
            )

        try:
            # Re-check whitelist after acquiring semaphore (mid-batch revocation)
            if not is_whitelisted(channel_id, "channels", config):
                extras = {}
                if batch_cursor is not None:
                    extras["already_downloaded_count"] = batch_cursor.get("already_downloaded_count", 0)
                    extras["pending_count"] = batch_cursor.get("pending_count", 0)
                raise NotWhitelistedError(
                    f"Channel {channel_id} was removed from whitelist mid-batch",
                    already_downloaded_count=extras.get("already_downloaded_count"),
                    pending_count=extras.get("pending_count"),
                )

            # Fetch the message
            msg = await client.get_messages(channel_id, ids=message_id)
            if msg is None:
                raise MessageNotFoundError(
                    f"Message {message_id} not found in channel {channel_id}"
                )

            if msg.document is None:
                raise MessageNotFoundError(
                    f"Message {message_id} has no document attachment"
                )

            doc = msg.document
            size = doc.size if hasattr(doc, "size") else 0
            mime_type = doc.mime_type if hasattr(doc, "mime_type") else "application/octet-stream"
            file_name = "unknown"

            if hasattr(doc, "attributes"):
                for attr in doc.attributes:
                    if hasattr(attr, "file_name") and attr.file_name:
                        file_name = attr.file_name
                        break

            # Check filters
            check_file(size, mime_type, config)

            # Build final path
            slug = channel_slug(channel_id, None)
            final_name = f"{message_id}_{safe_name(file_name)}"
            final_dir = Path(config.base_dir) / slug
            final_dir.mkdir(parents=True, exist_ok=True)
            final_path = final_dir / final_name

            # Download to temp file first, then move
            with tempfile.NamedTemporaryFile(
                dir=final_dir,
                prefix=f"_tmp_{message_id}_",
                delete=False,
            ) as tmp_f:
                tmp_path = Path(tmp_f.name)

            try:
                await client.download_media(msg, file=str(tmp_path))
                sha256 = _sha256_file(tmp_path)
                shutil.move(str(tmp_path), str(final_path))
            except Exception:
                # Clean up temp file on failure
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
                raise

            _upsert_index_row(conn, channel_id, message_id, size, sha256, str(final_path))

            return {
                "local_path": str(final_path),
                "already_downloaded": False,
                "size": size,
                "sha256": sha256,
            }

        finally:
            _download_semaphore.release()
