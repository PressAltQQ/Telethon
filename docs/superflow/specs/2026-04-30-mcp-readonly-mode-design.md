# MCP Bridge — Read-Only Mode (design)

**Date:** 2026-04-30
**Scope:** `mcp_bridge/` only. `telethon/` is not modified.
**Goal:** Provide a runtime-selectable mode in which the MCP bridge can ONLY read messages and download files. All write/communication operations (send, edit, delete, forward, reactions, joins, profile changes, ask_user, etc.) must be unreachable both at the MCP tool surface and at the underlying MTProto layer.

Communication needs are explicitly out of scope: a separate Telegram bot is the chosen channel for any LLM↔user interaction.

## 1. Architecture

Three layers of enforcement, all inside `mcp_bridge/` (no upstream divergence in `telethon/`):

1. **Entrypoint** — `mcp_bridge/server_readonly.py`: thin wrapper that sets `os.environ["MCP_READONLY"] = "1"` and calls `server.main()`. Provides a misuse-resistant launch path so production configs (systemd/launchd) cannot accidentally start the full server.
2. **Tool registration filter** — `mcp_bridge/server.py` reads `MCP_READONLY` and, when set, does not import or register `tools/bridge.py` (`send_message`, `ask_user`). The token-bucket rate-limiter is still constructed but is rebound to the download tool (see §4).
3. **RPC guard** — `mcp_bridge/readonly_guard.py` + `mcp_bridge/readonly_allowlist.py`. After `client.connect()` (and after optional login), the guard wraps `client._sender.send` with a checker that validates each `TLRequest` against an explicit allow-list of class names. Anything not allow-listed raises `PermissionError`. The guard is fail-closed: an unknown method is always blocked.

The guard is the load-bearing defense. Layers 1–2 keep the LLM surface clean; layer 3 is what makes the property hold even if some bridge code path accidentally calls `client.send_message(...)` directly.

## 2. Components

### 2.1 `mcp_bridge/server_readonly.py`
```python
import os
os.environ.setdefault("MCP_READONLY", "1")
from mcp_bridge.server import main
if __name__ == "__main__":
    main()
```
Invoked via `python -m mcp_bridge.server_readonly`.

### 2.2 `mcp_bridge/server.py` (changes)
- At tool-registration time, check `os.environ.get("MCP_READONLY") == "1"`.
- If set: skip importing `mcp_bridge.tools.bridge`; do not register `send_message` or `ask_user`.
- After `client.connect()` and before serving the first request, call `readonly_guard.install(client)`.

### 2.3 `mcp_bridge/readonly_allowlist.py`
A frozenset of TL request class names (strings). Curated, fail-closed. Initial contents:

**Auth / login (allowed — user requested login to work in read-only mode):**
`auth.SendCodeRequest`, `auth.ResendCodeRequest`, `auth.SignInRequest`, `auth.SignUpRequest`, `auth.CheckPasswordRequest`, `account.GetPasswordRequest`, `auth.ImportLoginTokenRequest`, `auth.ExportAuthorizationRequest`, `auth.ImportAuthorizationRequest`.

Explicitly NOT allowed: `auth.LogOutRequest` (destructive — kills auth_key), `auth.ExportLoginTokenRequest` (QR-login export can be coerced into account hijack), `auth.AcceptLoginTokenRequest` (authorize another device), `account.ResetAuthorizationRequest` (kill another session).

**Bootstrap / housekeeping:**
`PingRequest`, `PingDelayDisconnectRequest` (keepalive — bare names, top-level `telethon.tl.functions`), `help.GetConfigRequest`, `help.GetNearestDcRequest`, `updates.GetStateRequest`, `updates.GetDifferenceRequest`, `updates.GetChannelDifferenceRequest`.

**Reading messages and chats:**
`messages.GetHistoryRequest`, `messages.GetMessagesRequest`, `messages.SearchRequest`, `messages.SearchGlobalRequest`, `messages.GetDialogsRequest`, `messages.GetPeerDialogsRequest`, `messages.GetStickerSetRequest`, `channels.GetMessagesRequest`, `channels.GetFullChannelRequest`, `channels.GetChannelsRequest`, `channels.GetParticipantRequest`, `channels.GetParticipantsRequest`.

**Entity resolution:**
`contacts.ResolveUsernameRequest`, `contacts.GetContactsRequest`, `users.GetUsersRequest`, `users.GetFullUserRequest`, `photos.GetUserPhotosRequest`.

**Self-introspection (read-only):**
`account.GetAuthorizationsRequest`.

**Downloads:**
`upload.GetFileRequest`, `upload.GetCdnFileRequest`, `upload.ReuploadCdnFileRequest`, `upload.GetCdnFileHashesRequest`, `upload.GetWebFileRequest`.

Class names are stored as `ClassName` (last component) — the guard compares against `type(request).__name__`.

### 2.4 `mcp_bridge/readonly_guard.py`
- `install(client)`: replaces `client._sender.send` with a wrapper.
- `_check(request)`: accepts a single `TLRequest` or a list (Telethon batches via `MTProtoSender.send([req1, req2])`). For lists, every element must be allow-listed; otherwise the entire batch is rejected.
- On block: log `WARNING readonly_guard blocked: <ClassName>` to stderr and `raise ReadOnlyBlockedError(f"RPC blocked in read-only mode: {name}")` (`CODE = "READONLY_BLOCKED"`). The exception is a `BridgeError` subclass, so `dispatch_tool` catches it and returns a structured `{"error": {"code": "READONLY_BLOCKED", ...}}` response. The Telethon session is not torn down.

## 3. Data flow

1. `python -m mcp_bridge.server_readonly` sets `MCP_READONLY=1`, invokes `server.main()`.
2. `server.main()` constructs the encrypted session (`EncryptedSQLiteSession`, unchanged — H-4 still in force) and the `TelegramClient`.
3. Tool registration: `bridge.py` skipped; `poll.py` and `downloader.py` registered.
4. `client.connect()`. If session is unauthorized, the standard interactive login flow runs (allowed by allow-list).
5. `readonly_guard.install(client)` — from this point every outbound RPC is filtered.
6. Server enters its MCP request loop. Each tool call ultimately produces TL requests; allow-listed ones pass, blocked ones raise `PermissionError` with a clear message.

## 4. Rate limiting on downloads

Currently `tools/bridge.py` uses the token-bucket rate-limiter; `tools/downloader.py` only has `asyncio.Semaphore(1)` (concurrency, not rate). In read-only mode the bucket would otherwise become unused. Decision: in read-only mode, acquire one token from the existing `TokenBucket` at the start of each download tool call. Configuration values (`max_ops_per_minute`, `burst`) come from the same config; no new knobs.

Effect: protects against an LLM looping over `download_file` and exhausting bandwidth or triggering Telegram FLOOD_WAIT. Per-file size caps are out of scope here (separate task).

## 5. Error handling

- Blocked RPC → `ReadOnlyBlockedError("RPC blocked in read-only mode: <ClassName>")` (`CODE = "READONLY_BLOCKED"`). `dispatch_tool` catches it via the `except BridgeError` branch and returns a structured error response; session continues.
- Login disallowed methods (`auth.LogOutRequest`, `auth.ExportLoginTokenRequest`, `auth.AcceptLoginTokenRequest`, `account.ResetAuthorizationRequest`) → same `ReadOnlyBlockedError`.
- Download rate-limit exceeded → existing `RateLimitError` with `retry_after_seconds`.
- Allow-list import-time validation: at module load, `readonly_allowlist.py` resolves every entry to a real Telethon class via `importlib`. A typo or upstream removal raises `ImportError` at startup (loud, not silent).

## 6. Testing

All unit tests, no live network.

1. `tests/mcp_bridge/test_readonly_guard.py`
   - Allow-listed RPC passes through to wrapped sender.
   - `SendMessageRequest` → `PermissionError`.
   - Batch `[GetHistoryRequest, SendMessageRequest]` → entire batch blocked.
   - Log line contains the blocked class name.
2. `tests/mcp_bridge/test_readonly_server.py`
   - With `MCP_READONLY=1`: registered tool names include `poll_chat_since` and download tool but exclude `send_message` and `ask_user`.
   - Without the env var: `send_message` and `ask_user` are present (regression guard).
   - Rate-limiter is acquired in download tool when running in read-only mode.
3. `tests/mcp_bridge/test_readonly_allowlist.py`
   - For every name in `READONLY_ALLOWLIST`, the corresponding Telethon class can be imported. Catches typos and upstream renames.
4. CI smoke: `python -m mcp_bridge.server_readonly --help` exits 0.

## 7. Out of scope

- Hardening `telethon/` itself (would diverge from upstream; option C from brainstorm was deferred to bridge-level guard).
- Per-file or per-hour download volume caps.
- Audit log of blocked attempts beyond stderr WARNING.
- Bot-side communication channel (separate project).

## 8. Migration & compatibility

- Sessions are file-compatible between full and read-only modes (`EncryptedSQLiteSession` unchanged).
- No new runtime dependencies.
- No changes to existing tool signatures or to `telethon/`.
- CI matrix gains one smoke test; no other config changes required.
