# Telethon MCP Bridge — Install Runbook

Personal-use MCP stdio server on top of this Telethon fork. Exposes
Telegram file downloads, Q&A messaging, and realtime chat polling to
Claude Code (or any MCP-capable client) via stdio.

**Security posture:** stdio-only transport, encrypted session at rest,
whitelisted channels/chats, single-instance lock, no network ports.

---

## Prerequisites

- macOS 12+ or Linux (Ubuntu 22.04+). Windows is not supported.
- Python 3.11 or 3.13.
- [uv](https://github.com/astral-sh/uv) package manager.
- Your own Telegram `api_id` and `api_hash` from
  [my.telegram.org](https://my.telegram.org/auth).

---

## Install Runbook (SC6 — target: < 15 min wall clock)

Follow these steps in order. Each step includes a one-line success check.

### Step 1 — Clone

```bash
git clone git@github.com:PressAltQQ/Telethon.git
cd Telethon
# check: pwd ends with /Telethon
```

### Step 2 — Install uv (one-time; skip if already present)

```bash
curl -LsSf https://astral.sh/uv/0.4.30/install.sh | sh
# check: uv --version  →  0.4.30
```

Pin to a specific version for reproducibility. If you already have uv,
`uv --version` must show 0.4.20 or later.

### Step 3 — Sync dependencies

```bash
uv sync --frozen
# check: .venv/bin/python -c "import telethon, mcp_bridge"  (exits 0)
```

`--frozen` ensures the exact versions in `uv.lock` are installed,
not the latest available.

### Step 4 — Create config

```bash
mkdir -p ~/.config/telethon-mcp-bridge
cp docs/mcp_bridge/config.example.toml ~/.config/telethon-mcp-bridge/config.toml
chmod 600 ~/.config/telethon-mcp-bridge/config.toml
$EDITOR ~/.config/telethon-mcp-bridge/config.toml
# check: stat -f '%A' ~/.config/telethon-mcp-bridge/config.toml  →  600
```

Edit at minimum:
- `[telegram] api_id` — your integer API ID
- `[telegram] api_hash` — your API hash (or set `TELETHON_API_HASH` env var)
- `[whitelist] channels` — list of channel IDs to allow downloads from
- `[whitelist] read_chats` — list of chat IDs you want to poll
- `[downloader] base_dir` — where downloaded files will be stored

### Step 5 — First run (initial login + session encryption)

```bash
.venv/bin/python -m mcp_bridge --first-run
# Enter your phone number when prompted.
# Enter the SMS or app code when prompted.
# Bridge prints: "first-run login complete. Re-run without --first-run to start the MCP server."
# The process then exits with code 0.
# check: re-run without --first-run and the MCP server starts normally.
```

The session key is stored in your OS keyring (macOS Keychain / Linux
Secret Service). The session file on disk is encrypted with
ChaCha20-Poly1305. Stealing the session file alone is not enough to
authenticate — the keyring entry is required.

**Headless environments:** set `TELETHON_SESSION_KEY` (base64, 32 bytes)
and `TELETHON_SESSION_SALT` (base64, 32 bytes) as environment variables
instead of using the keyring.

### Step 6 — Verify MCP endpoint

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  .venv/bin/python -m mcp_bridge
# check: response JSON lists list_channel_files, download_file,
#        send_message, ask_user, poll_chat_since
```

> **Note:** This step will hang after the response — press Ctrl-C once you see the tools list. Future improvement: dedicated `list_tools_cli` subcommand.

---

## Optional Step 7 — macOS launchd service

Copy the template and fill in paths, then load:

```bash
cp docs/mcp_bridge/launchd.plist.template \
   ~/Library/LaunchAgents/com.user.telethon-mcp-bridge.plist
# Edit the plist: replace {{VENV_BIN}}, {{CONFIG_PATH}}, {{LOG_PATH}}
launchctl load ~/Library/LaunchAgents/com.user.telethon-mcp-bridge.plist
# check: launchctl list | grep telethon-mcp-bridge
```

See `docs/mcp_bridge/launchd.plist.template` for the full template.

---

## Configuration Reference

Full schema in `docs/mcp_bridge/config.example.toml`.

Key sections:

| Section | Key | Default | Description |
|---|---|---|---|
| `[telegram]` | `api_id` | required | Integer API ID |
| `[telegram]` | `api_hash` | required | String API hash (or `TELETHON_API_HASH` env) |
| `[telegram]` | `session_name` | `"main"` | Session file basename |
| `[session]` | `key_source` | `"keyring"` | `"keyring"` or `"env"` |
| `[downloader]` | `base_dir` | required | Absolute path for downloaded files |
| `[downloader]` | `max_file_size_mb` | `100` | Max file size in MB |
| `[whitelist]` | `channels` | `[]` | Channel IDs for download tools |
| `[whitelist]` | `read_chats` | `[]` | Chat IDs for poll_chat_since |
| `[whitelist]` | `write_chats` | `[]` | Chat IDs for send_message |
| `[whitelist]` | `ask_chats` | `[]` | Chat IDs for ask_user |
| `[bridge]` | `poll_buffer_size` | `256` | Per-chat ring buffer size |
| `[bridge]` | `ask_fallback` | `"strict"` | Reply matching strategy |
| `[rate_limit]` | `max_ops_per_minute` | `30` | Token bucket refill rate |
| `[rate_limit]` | `burst` | `5` | Token bucket burst capacity |

---

## Available Tools

| Tool | Description |
|---|---|
| `list_channel_files` | List documents in a whitelisted channel |
| `download_file` | Download a file by channel + message ID (idempotent) |
| `send_message` | Send a text message to a whitelisted chat |
| `ask_user` | Send a message and wait for a reply (blocking, up to timeout) |
| `poll_chat_since` | Long-poll for new messages since a given message ID |

### poll_chat_since

```json
{
  "chat_id": 12345,
  "since_message_id": 100,
  "timeout_ms": 1500
}
```

Response:
```json
{
  "messages": [
    {
      "message_id": 101,
      "from_id": 42,
      "text": "hello",
      "reply_to_msg_id": null,
      "date": "2026-01-01T12:00:00",
      "has_media": false,
      "media_summary": null
    }
  ],
  "next_since_message_id": 101,
  "server_wait_ms": 12.3
}
```

On timeout with no new messages: `messages` is empty and
`next_since_message_id` equals the input value. Re-poll immediately.

Error codes specific to this tool:
- `POLL_ALREADY_ACTIVE` — another poll for the same `(connection, chat_id)` is in flight.
- `POLL_CURSOR_LOST` — `since_message_id` pre-dates the ring buffer; re-fetch history.
- `NOT_WHITELISTED` — `chat_id` is not in `read_chats`.

---

## Why polling and not push (server-initiated notifications)?

A spike conducted on 2026-04-18 (`docs/mcp_bridge/mcp-notifications-spike.md`)
confirmed that Claude Code's MCP stdio client does **not** reliably surface
server-initiated notifications into the live agent transcript.

v1 therefore uses **long-polling** as the primary realtime API. The
`poll_chat_since` tool returns new messages as soon as they arrive (typically
< 100 ms after the message is buffered by the update handler), or after the
configured `timeout_ms` if none arrive.

SC3 contract: p95 delivery latency < 2000 ms in the synthetic harness
(tested in `tests/mcp_bridge/test_realtime_latency.py`).

Server-push subscribe is deferred to v2 once Claude Code push support is confirmed.

---

## Why stdio-only transport?

Using HTTP instead of stdio would expose a network port on localhost. Any
process on the machine — including malware, Docker containers, browser
JavaScript (via `fetch("http://localhost:PORT/...")`) — could access the
Telegram session. The attack surface for localhost HTTP servers is large
and well-documented.

With stdio:

- **No port is opened.** No network layer to misconfigure.
- **Access is process-scoped.** Only the parent MCP client that holds the
  pipe file descriptors can communicate with the bridge.
- **Browser attacks are impossible.** There is no port for a browser to target.
- **Daemon exit closes the channel.** When the MCP client exits, stdin EOF
  propagates and the bridge shuts down cleanly.

This analysis applies specifically to personal-use MCP servers that hold
session credentials. It is not a general statement about all MCP servers.

---

## Lockdown guidance — protecting your session

The session file (`{session_name}.session`) is an encrypted SQLite database.
It is protected by two independent controls:

1. **Filesystem permissions** — `chmod 0o600` is applied on creation. Only
   the owner can read or write it.
2. **Encryption at rest** — `EncryptedSQLiteSession` encrypts `auth_key`
   and entity PII columns using ChaCha20-Poly1305 with a key derived from
   a 32-byte root key stored in the OS keyring.

**Stealing the session file is not sufficient to authenticate** — the
attacker also needs the keyring entry (or `TELETHON_SESSION_KEY` env var).

Additional recommendations:

- Store the config file at `chmod 600` (the bridge enforces this at startup).
- Do not use `TELETHON_API_HASH` in shell history; use a secrets manager or
  store it only in the config file.
- Keep `downloads/` out of cloud sync (it may contain sensitive documents).
  The default `.gitignore` excludes `downloads/`.
- Rotate the session key annually or after any suspected compromise by
  running `--migrate-to-encrypted` with a freshly generated key.
- The `.session.lock` file is advisory; do not delete it while the daemon
  is running.

---

## Network filesystem caveat

The session file must reside on a **local filesystem**. `fcntl.flock` on
NFS/SMB is advisory, meaning single-instance enforcement silently breaks.
The config loader checks for NFS/SMB and refuses to start with `CONFIG_INVALID`
if a network mount is detected.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `SESSION_LOCKED` on startup | Another instance running | Check `ps aux | grep mcp_bridge`; kill the holder |
| `KEYRING_UNAVAILABLE` | No keyring on headless machine | Set `TELETHON_SESSION_KEY` + `TELETHON_SESSION_SALT` env vars |
| `CONFIG_INVALID` on perms | Config file is world-readable | `chmod 600 ~/.config/telethon-mcp-bridge/config.toml` |
| `POLL_CURSOR_LOST` | Cursor too far behind ring buffer | Re-fetch history with `list_channel_files` from the last known good message |
| Latency > 2 s | Event loop contention | Check concurrent download (semaphore) or large message processing |
