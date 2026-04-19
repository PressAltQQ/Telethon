<!--
ARCHIVED 2026-04-19 — moved from repo root (original name: 01_lockdown_guide.md).
STALENESS WARNING: This document recommends `telethon==1.36.0`; this fork is at 1.42.0.
Do not act on the pin. Retained for historical context and audit trail only.
Current lockdown posture lives in `docs/mcp_bridge/README.md` and `SECURITY_AUDIT_REPORT.md`.
-->

# Telethon Lockdown Guide: Safe Bulk File Downloading

## 1. Protecting Session Files (Finding #1: CRITICAL)

Telethon session files (`.session`) contain raw auth keys in plaintext SQLite. Anyone who copies your session file **owns your Telegram account**.

### 1.1 Encrypt the session file at rest

Use Telethon's built-in support for custom session storage. Wrap the SQLite session so the file on disk is encrypted:

```python
import os
import hashlib
from telethon.sessions import SQLiteSession

SESSION_DIR = "/secure/path/sessions"
SESSION_NAME = "downloader"

# Restrict directory permissions BEFORE creating the session
os.makedirs(SESSION_DIR, mode=0o700, exist_ok=True)

# Create session inside the restricted directory
session_path = os.path.join(SESSION_DIR, SESSION_NAME)
client = TelegramClient(session_path, api_id, api_hash)
```

After session creation, lock the file:

```python
import stat

session_file = session_path + ".session"
if os.path.exists(session_file):
    os.chmod(session_file, stat.S_IRUSR | stat.S_IWUSR)  # 0o600 — owner only
```

### 1.2 Use an in-memory session for ephemeral runs

If you can re-authenticate each time (e.g., via bot token), avoid writing to disk entirely:

```python
from telethon.sessions import StringSession

# First run: generate a string session and store it in a secrets manager
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print(client.session.save())  # Store THIS string in a vault, not on disk

# Subsequent runs: load from environment / secrets manager
session_str = os.environ["TELEGRAM_SESSION"]  # from vault / env
client = TelegramClient(StringSession(session_str), api_id, api_hash)
```

Store the string session in a proper secrets manager (macOS Keychain, 1Password CLI, HashiCorp Vault, AWS Secrets Manager) — NOT in `.env` files or plaintext configs.

### 1.3 Delete session files when done

```python
import atexit

def cleanup_session():
    for ext in [".session", ".session-journal"]:
        path = session_path + ext
        if os.path.exists(path):
            # Overwrite before delete to prevent recovery
            with open(path, "wb") as f:
                f.write(os.urandom(os.path.getsize(path)))
            os.remove(path)

atexit.register(cleanup_session)
```

---

## 2. Limiting Telethon to Read-Only / Download-Only (Principle of Least Privilege)

Telethon exposes the full Telegram API. You must build a restricted wrapper that prevents all write operations.

### 2.1 Use a dedicated account or bot

- **Best option**: Create a Telegram bot via @BotFather, add it to the channel as admin with **NO permissions except "Read Messages"**. Bots cannot initiate contact, change account settings, etc.
- **Alternative**: Use a dedicated throwaway account, not your personal one.

### 2.2 Wrap the client in a read-only facade

```python
from telethon import TelegramClient
from telethon.tl.types import Channel
import os

class ReadOnlyDownloader:
    """Wraps TelegramClient exposing ONLY read/download operations."""

    ALLOWED_CHANNEL_IDS: set[int] = set()  # Whitelist of channel IDs

    def __init__(self, session, api_id, api_hash):
        self._client = TelegramClient(session, api_id, api_hash)

    async def start(self):
        await self._client.start()

    async def disconnect(self):
        await self._client.disconnect()

    async def list_files(self, channel_id: int, limit: int = 100, offset_id: int = 0):
        """List messages with media in a whitelisted channel."""
        self._assert_allowed(channel_id)
        if limit > 500:
            raise ValueError("limit cannot exceed 500")

        messages = []
        async for msg in self._client.iter_messages(
            channel_id, limit=limit, offset_id=offset_id
        ):
            if msg.media:
                messages.append({
                    "message_id": msg.id,
                    "date": msg.date.isoformat(),
                    "filename": getattr(msg.media, "document", None)
                               and self._get_filename(msg),
                    "size": self._get_size(msg),
                    "mime_type": self._get_mime(msg),
                })
        return messages

    async def download_file(self, channel_id: int, message_id: int, output_dir: str,
                            max_size_mb: int = 2048):
        """Download a single file from a whitelisted channel."""
        self._assert_allowed(channel_id)
        output_dir = self._validate_output_dir(output_dir)

        msg = await self._client.get_messages(channel_id, ids=message_id)
        if not msg or not msg.media:
            raise ValueError(f"No media in message {message_id}")

        # Check file size before downloading
        size = self._get_size(msg)
        if size and size > max_size_mb * 1024 * 1024:
            raise ValueError(f"File too large: {size} bytes (max {max_size_mb} MB)")

        path = await self._client.download_media(
            msg,
            file=output_dir,
        )

        # Validate the final path didn't escape output_dir
        if path:
            real_path = os.path.realpath(path)
            real_output = os.path.realpath(output_dir)
            if not real_path.startswith(real_output + os.sep):
                os.remove(real_path)
                raise SecurityError(f"Path traversal detected: {real_path}")

        return path

    def _assert_allowed(self, channel_id: int):
        if self.ALLOWED_CHANNEL_IDS and channel_id not in self.ALLOWED_CHANNEL_IDS:
            raise PermissionError(f"Channel {channel_id} is not in the whitelist")

    def _validate_output_dir(self, output_dir: str) -> str:
        real = os.path.realpath(output_dir)
        # Prevent writing outside designated base directory
        allowed_base = os.path.realpath(os.environ.get("DOWNLOAD_BASE", "/tmp/tg-downloads"))
        if not real.startswith(allowed_base + os.sep) and real != allowed_base:
            raise SecurityError(f"Output dir {real} is outside allowed base {allowed_base}")
        os.makedirs(real, mode=0o750, exist_ok=True)
        return real

    @staticmethod
    def _get_filename(msg):
        if msg.document:
            for attr in msg.document.attributes:
                if hasattr(attr, "file_name"):
                    return attr.file_name
        return None

    @staticmethod
    def _get_size(msg):
        if msg.document:
            return msg.document.size
        if msg.photo:
            return None  # Photo sizes not reliably known beforehand
        return None

    @staticmethod
    def _get_mime(msg):
        if msg.document:
            return msg.document.mime_type
        return "image/jpeg" if msg.photo else None


class SecurityError(Exception):
    pass
```

**Key point**: the `TelegramClient` object itself is private (`self._client`). User code only calls `list_files()` and `download_file()`. There are no methods for sending messages, editing profile, managing contacts, or joining channels.

---

## 3. Avoiding Telegram ToS Violations and Account Bans

### 3.1 Rate limiting and flood wait handling

```python
from telethon.errors import FloodWaitError
import asyncio
import logging

logger = logging.getLogger(__name__)

# Global rate limiter: max N requests per second
RATE_LIMIT_DELAY = 1.0  # seconds between downloads

async def safe_download(downloader, channel_id, message_id, output_dir):
    try:
        path = await downloader.download_file(channel_id, message_id, output_dir)
        await asyncio.sleep(RATE_LIMIT_DELAY)  # Mandatory cooldown
        return path
    except FloodWaitError as e:
        wait_time = e.seconds + 5  # Add buffer
        logger.warning(f"Flood wait: sleeping {wait_time}s")
        await asyncio.sleep(wait_time)
        return await safe_download(downloader, channel_id, message_id, output_dir)
```

### 3.2 Critical rules to avoid bans

1. **Always handle `FloodWaitError`** — never retry immediately. Wait the full duration + buffer.
2. **Use conservative delays**: 1-2 seconds between downloads minimum. For large bulk jobs, 3-5 seconds.
3. **Use official API parameters**: Set `api_id` and `api_hash` from https://my.telegram.org. Do NOT use leaked/shared credentials.
4. **Set realistic device info** (avoid obviously fake values):

```python
from telethon import TelegramClient

client = TelegramClient(
    session,
    api_id,
    api_hash,
    device_model="Desktop",
    system_version="macOS 15.0",
    app_version="1.0",
    lang_code="en",
    system_lang_code="en-US",
)
```

5. **Do not create new sessions repeatedly** — reuse your session file.
6. **Do not run multiple clients on the same account simultaneously.**
7. **Do not download thousands of files in a single burst** — spread over time, use batch sizes of 50-100 with pauses.
8. **If the channel is public, prefer `https://t.me/` links over channel IDs** — Telegram treats public channel access more leniently.

---

## 4. Crypto Dependency: Install `cryptg` (Finding #4: HIGH)

Telethon has a pure-Python AES-IGE fallback (`pyaes`). This is:
- **Slow** (100x slower than native)
- **Not audited** — custom crypto implementations are a security risk

**Fix**: install the native C extension:

```bash
pip install cryptg
```

Verify it's being used:

```python
import telethon.crypto
# If cryptg is installed, Telethon uses it automatically.
# To verify at runtime:
try:
    import cryptg
    print(f"cryptg {cryptg.__version__} loaded — native AES-IGE active")
except ImportError:
    raise RuntimeError("cryptg not installed! Refusing to run with pyaes fallback.")
```

Add to your startup:

```python
def verify_crypto():
    try:
        import cryptg  # noqa: F401
    except ImportError:
        raise SystemExit(
            "FATAL: cryptg is not installed. "
            "Install it with: pip install cryptg\n"
            "Running with the pure-Python AES fallback is insecure and slow."
        )

verify_crypto()
```

---

## 5. Safe File Handling (Finding #5: MEDIUM-HIGH)

### 5.1 Path traversal prevention

Telethon uses filenames from Telegram's servers to save files. A malicious filename like `../../etc/cron.d/evil` could write outside your download directory.

```python
import os
import re

def sanitize_filename(filename: str) -> str:
    """Remove path separators and dangerous characters from filenames."""
    # Strip directory components
    filename = os.path.basename(filename)
    # Remove null bytes
    filename = filename.replace("\x00", "")
    # Whitelist: keep only safe characters
    filename = re.sub(r'[^\w\s\-\.\(\)\[\]]', '_', filename)
    # Prevent hidden files
    filename = filename.lstrip(".")
    # Truncate to reasonable length
    filename = filename[:200]
    return filename or "unnamed_file"
```

### 5.2 File size limits

```python
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB — Telegram's own limit

def check_disk_space(output_dir: str, required_bytes: int):
    """Ensure enough disk space before downloading."""
    stat = os.statvfs(output_dir)
    available = stat.f_bavail * stat.f_frsize
    if required_bytes and available < required_bytes * 1.1:  # 10% buffer
        raise IOError(f"Insufficient disk space: {available} available, {required_bytes} needed")
```

### 5.3 Post-download validation

```python
def validate_downloaded_file(path: str, output_dir: str):
    """Validate a downloaded file hasn't escaped the output directory."""
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(output_dir)

    if not real_path.startswith(real_base + os.sep):
        os.remove(real_path)
        raise SecurityError(f"Downloaded file escaped output directory: {real_path}")

    # Check for suspicious file types
    dangerous_extensions = {".exe", ".bat", ".cmd", ".scr", ".pif", ".com", ".ps1", ".sh"}
    _, ext = os.path.splitext(real_path)
    if ext.lower() in dangerous_extensions:
        logger.warning(f"Downloaded potentially dangerous file type: {ext}")
```

---

## 6. Network Security (Findings #7, #8)

### 6.1 SSRF prevention

If you process any URLs from downloaded messages (e.g., web page previews), never fetch them server-side without validation. For pure file downloading, this is less of a concern — but if you ever expand functionality:

```python
import ipaddress
from urllib.parse import urlparse

BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}

def is_safe_url(url: str) -> bool:
    """Block internal/private network URLs to prevent SSRF."""
    parsed = urlparse(url)
    if parsed.hostname in BLOCKED_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass  # hostname, not IP — OK
    return True
```

### 6.2 Proxy configuration (if needed)

```python
import socks

# Route Telegram traffic through a SOCKS5 proxy
client = TelegramClient(
    session,
    api_id,
    api_hash,
    proxy=(socks.SOCKS5, "127.0.0.1", 9050),  # e.g., Tor or SSH tunnel
)
```

### 6.3 Hardcoded RSA keys (Finding #8)

The RSA keys hardcoded in Telethon are Telegram's official server public keys — they are used to verify the server's identity during key exchange. This is **expected behavior**, not a vulnerability per se. However:

- **Pin to a known version of Telethon** in your `requirements.txt` (e.g., `telethon==1.36.0`).
- If Telegram rotates keys, update Telethon — do not patch keys manually.
- Verify Telethon releases via PyPI signatures or GitHub release hashes.

---

## Summary Checklist

| Risk | Mitigation | Priority |
|------|-----------|----------|
| Plaintext session files | `StringSession` + secrets manager + `0o600` perms | Do first |
| Full API surface exposed | `ReadOnlyDownloader` wrapper, whitelist channels | Do first |
| Python AES fallback | `pip install cryptg`, fail if missing | Do first |
| Path traversal | Sanitize filenames, validate paths post-download | Do first |
| Account ban risk | Rate limiting, flood wait handling, conservative delays | Do first |
| SSRF | Not relevant for pure downloads; guard if expanding | Low priority |
| Hardcoded RSA keys | Pin Telethon version, update promptly | Low priority |
| Device fingerprinting | Set realistic device info | Low priority |
