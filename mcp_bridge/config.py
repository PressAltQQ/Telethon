"""
TOML config loader for the MCP bridge.

Reads from $TELETHON_MCP_CONFIG or ~/.config/telethon-mcp-bridge/config.toml.
Enforces 0o600 permissions (non-Windows), validates schema, supports env override.
"""
from __future__ import annotations

import logging
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

from mcp_bridge.errors import ConfigInvalidError

__log__ = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "telethon-mcp-bridge" / "config.toml"

_REQUIRED_TABLES = ["telegram", "session", "downloader", "whitelist", "rate_limit", "logging"]


@dataclass
class Config:
    # [telegram]
    api_id: int
    api_hash: str
    session_name: str

    # [session]
    key_source: str

    # [downloader]
    base_dir: Path
    max_file_size_mb: int
    allowed_mime_types: List[str]
    denied_mime_types: List[str]

    # [whitelist]
    channels: List[int]
    read_chats: List[int]
    write_chats: List[int]
    ask_chats: List[int]

    # [rate_limit]
    max_ops_per_minute: int
    burst: int
    concurrent_queue_wait_seconds: float = 2.0

    # [logging]
    log_level: str = "INFO"
    log_file: Path = field(default_factory=lambda: Path("~/.local/state/telethon-mcp-bridge/bridge.log").expanduser())

    # Optional session fields
    session_path: Path | None = None


def _resolve_config_path() -> Path:
    env_path = os.environ.get("TELETHON_MCP_CONFIG")
    if env_path:
        return Path(env_path)
    return _DEFAULT_CONFIG_PATH


def _check_permissions(path: Path) -> None:
    """Reject config files with permissions wider than 0o600 (non-Windows)."""
    if sys.platform == "win32":
        return
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise ConfigInvalidError(f"Cannot stat config file {path}: {exc}") from exc

    mode = stat.S_IMODE(file_stat.st_mode)
    if mode & 0o177:  # any bits set beyond owner rw (0o600)
        raise ConfigInvalidError(
            f"Config file {path} has permissions {oct(mode)} — must be 0o600. "
            "Run: chmod 600 " + str(path)
        )


def _check_nfs_smb(session_path: Path) -> None:
    """Warn or refuse if session_path is on a non-local filesystem.

    On Linux, checks /proc/mounts. On macOS, uses 'mount' command output.
    If detection is ambiguous, logs a warning but does not hard-fail.
    """
    parent = session_path.parent
    try:
        parent_resolved = parent.resolve()
    except OSError:
        __log__.warning("Cannot resolve session path parent %s — skipping NFS check", parent)
        return

    if sys.platform == "linux":
        _check_nfs_smb_linux(parent_resolved)
    elif sys.platform == "darwin":
        _check_nfs_smb_macos(parent_resolved)
    # Other platforms: skip (no detection)


def _check_nfs_smb_linux(resolved_path: Path) -> None:
    """Check /proc/mounts for NFS/SMB mounts covering the path."""
    try:
        with open("/proc/mounts") as f:
            mounts_text = f.read()
    except OSError:
        __log__.warning("Cannot read /proc/mounts — skipping NFS check")
        return

    _check_mounts_text(str(resolved_path), mounts_text)


def _check_nfs_smb_macos(resolved_path: Path) -> None:
    """Check 'mount' output for NFS/SMB mounts covering the path."""
    import subprocess

    try:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        mounts_text = result.stdout
    except Exception:
        __log__.warning("Cannot run 'mount' — skipping NFS check")
        return

    _check_mounts_text(str(resolved_path), mounts_text)


def _check_mounts_text(path_str: str, mounts_text: str) -> None:
    """Parse mount output and raise if a non-local FS type covers the path."""
    network_types = {"nfs", "nfs4", "nfs3", "cifs", "smb", "smbfs", "afp"}

    best_match_len = -1
    best_match_fstype = None

    for line in mounts_text.splitlines():
        parts = line.split()
        # Linux /proc/mounts: device mountpoint fstype options dump pass
        # macOS mount: device on mountpoint (fstype, options)
        mount_point = None
        fs_type = None

        if len(parts) >= 3 and parts[1].startswith("/"):
            # Linux format
            mount_point = parts[1]
            fs_type = parts[2].lower()
        elif " on " in line and " (" in line:
            # macOS format: device on mountpoint (type, ...)
            try:
                on_idx = line.index(" on ")
                paren_idx = line.index(" (")
                mount_point = line[on_idx + 4:paren_idx].strip()
                after_paren = line[paren_idx + 2:]
                fs_type = after_paren.split(",")[0].strip().lower()
            except (ValueError, IndexError):
                continue

        if mount_point and fs_type:
            mp_prefix = mount_point.rstrip("/") + "/"
            if (path_str == mount_point or path_str.startswith(mp_prefix)) and len(mount_point) > best_match_len:
                best_match_len = len(mount_point)
                best_match_fstype = fs_type

    if best_match_fstype and best_match_fstype in network_types:
        raise ConfigInvalidError(
            f"session_path must be on a local filesystem; NFS/SMB locks are advisory. "
            f"Detected filesystem type: {best_match_fstype!r} for path {path_str!r}. "
            "Move the session to a local disk."
        )


def load_config(path: Path | None = None) -> Config:
    """Load and validate config from TOML file.

    Raises ConfigInvalidError on schema violations or permission issues.
    """
    if path is None:
        path = _resolve_config_path()

    _check_permissions(path)

    try:
        raw = tomllib.loads(path.read_text())
    except OSError as exc:
        raise ConfigInvalidError(f"Cannot read config file {path}: {exc}") from exc
    except Exception as exc:
        raise ConfigInvalidError(f"Cannot parse config file {path}: {exc}") from exc

    # Validate required tables
    for table in _REQUIRED_TABLES:
        if table not in raw:
            raise ConfigInvalidError(
                f"Config missing required table [{table}] in {path}"
            )

    telegram = raw["telegram"]
    session = raw["session"]
    downloader = raw["downloader"]
    whitelist = raw["whitelist"]
    rate_limit = raw["rate_limit"]
    logging_cfg = raw["logging"]

    # api_hash env override
    api_hash = os.environ.get("TELETHON_API_HASH") or telegram.get("api_hash", "")

    base_dir = Path(downloader.get("base_dir", "~/telethon-downloads")).expanduser()

    session_name = telegram.get("session_name", "main")
    key_source = session.get("key_source", "keyring")

    # Build session_path from base_dir and session_name for NFS check
    session_path = base_dir / f"{session_name}.session"

    # NFS/SMB check on session path
    _check_nfs_smb(session_path)

    log_file_raw = logging_cfg.get("file", "~/.local/state/telethon-mcp-bridge/bridge.log")
    log_file = Path(log_file_raw).expanduser()

    concurrent_queue_wait = float(
        downloader.get("concurrent_queue_wait_seconds", 2.0)
    )

    config = Config(
        api_id=int(telegram["api_id"]),
        api_hash=api_hash,
        session_name=session_name,
        key_source=key_source,
        base_dir=base_dir,
        max_file_size_mb=int(downloader.get("max_file_size_mb", 100)),
        allowed_mime_types=list(downloader.get("allowed_mime_types", ["application/pdf", "image/*", "document/*"])),
        denied_mime_types=list(downloader.get("denied_mime_types", ["video/*"])),
        channels=list(whitelist.get("channels", [])),
        read_chats=list(whitelist.get("read_chats", [])),
        write_chats=list(whitelist.get("write_chats", [])),
        ask_chats=list(whitelist.get("ask_chats", [])),
        max_ops_per_minute=int(rate_limit.get("max_ops_per_minute", 30)),
        burst=int(rate_limit.get("burst", 5)),
        concurrent_queue_wait_seconds=concurrent_queue_wait,
        log_level=logging_cfg.get("level", "INFO"),
        log_file=log_file,
        session_path=session_path,
    )

    return config
