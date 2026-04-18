"""
Whitelist gates, file-size/MIME gates, and path sanitization helpers.

safe_name() and channel_slug() are the bridge's own implementations,
independent of the Telethon-internal _safe_join from Sprint 2.
"""
from __future__ import annotations

import fnmatch
import re
import unicodedata
from pathlib import PurePosixPath

from mcp_bridge.errors import FileTooLargeError, UnsupportedMimeError

# Windows reserved names (case-insensitive)
_WINDOWS_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\.|$)",
    re.IGNORECASE,
)

_MAX_NAME_LENGTH = 120


def is_whitelisted(chat_id: int, list_name: str, config) -> bool:
    """Return True if chat_id is in the named whitelist.

    list_name must be one of: 'channels', 'read_chats', 'write_chats', 'ask_chats'.
    """
    mapping = {
        "channels": config.channels,
        "read_chats": config.read_chats,
        "write_chats": config.write_chats,
        "ask_chats": config.ask_chats,
    }
    lst = mapping.get(list_name)
    if lst is None:
        return False
    return chat_id in lst


def _mime_matches(mime: str, patterns: list[str]) -> bool:
    """Return True if mime matches any of the glob patterns."""
    for pattern in patterns:
        if fnmatch.fnmatch(mime, pattern):
            return True
    return False


def check_file(size: int, mime: str, config) -> None:
    """Raise FileTooLargeError or UnsupportedMimeError if the file fails filters.

    denied_mime_types takes precedence over allowed_mime_types.
    """
    max_bytes = config.max_file_size_mb * 1024 * 1024
    if size > max_bytes:
        raise FileTooLargeError(
            f"File size {size} bytes exceeds limit of {max_bytes} bytes "
            f"({config.max_file_size_mb} MB)"
        )

    if config.denied_mime_types and _mime_matches(mime, config.denied_mime_types):
        raise UnsupportedMimeError(
            f"MIME type {mime!r} is in the denied list"
        )

    if config.allowed_mime_types and not _mime_matches(mime, config.allowed_mime_types):
        raise UnsupportedMimeError(
            f"MIME type {mime!r} is not in the allowed list"
        )


def safe_name(untrusted: str) -> str:
    """Return a filesystem-safe version of the given filename.

    - NFKC normalize
    - Strip null bytes
    - Replace path separators (/ and \\) with _
    - Rewrite Windows reserved names with _reserved_ prefix
    - Truncate to 120 chars preserving extension
    """
    # NFKC normalize
    name = unicodedata.normalize("NFKC", untrusted)
    # Strip null bytes
    name = name.replace("\x00", "")
    # Replace path separators
    name = name.replace("/", "_").replace("\\", "_")

    if not name:
        return "_"

    # Rewrite Windows reserved names
    if _WINDOWS_RESERVED.match(name):
        name = "_reserved_" + name

    # Truncate to 120 chars, preserving extension
    if len(name) > _MAX_NAME_LENGTH:
        path = PurePosixPath(name)
        ext = path.suffix  # e.g. ".pdf"
        stem = path.stem
        if ext:
            max_stem = _MAX_NAME_LENGTH - len(ext)
            name = stem[:max_stem] + ext
        else:
            name = name[:_MAX_NAME_LENGTH]

    return name


def channel_slug(channel_id: int, channel_name: str | None) -> str:
    """Create a safe directory name: {channel_id}_{safe_name or 'unnamed'}."""
    if channel_name:
        slug_part = safe_name(channel_name)
    else:
        slug_part = "unnamed"
    return f"{channel_id}_{slug_part}"
