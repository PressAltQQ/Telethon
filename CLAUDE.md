# Telethon (PressAltQQ fork) — Claude Instructions

## Project Overview
Security-hardened fork of Telethon, a pure-Python asyncio MTProto/Telegram client. Upstream: `codeberg.org/Lonami/Telethon`. This fork: `github.com/PressAltQQ/Telethon`. The fork exists to harden MTProto handling and diverges from upstream **only by security posture** — no feature divergence.

Stack: Python 3.11+ (3.13 dev), asyncio. Runtime deps `cryptography>=46,<47`, `keyring>=24,<26`, `mcp>=0.9,<2`, `rsa==4.9`. Optional (`optional-requirements.txt`): `cryptg`, `python-socks[asyncio]`, `hachoir`, `pillow`, `isal`. Build: `setup.py` + `pyproject.toml`. Package manager: `uv` (lock at `uv.lock`). CI: `.github/workflows/ci.yml` runs ruff + pytest (py3.11 + py3.13) + pip-audit on every push/PR. `pyaes` is dev-only fallback, opt-in via `TELETHON_ALLOW_PYAES=1` (N-3).

Framework claims are verified against imports (`cryptography` in `telethon/crypto/aes.py` / `aesctr.py`; `asyncio` in `telethon/client/telegrambaseclient.py:4`).

The `mcp_bridge/` package (added in MCP bridge run, 2026-04-19) is a sibling to `telethon/`. It exposes Telegram Q&A + downloads to LLM clients over stdio MCP. Do NOT have `telethon/` import from `mcp_bridge/`. The reverse is fine.

## Key Rules
- **Never edit generated TL code by hand.** `telethon/tl/**` and `telethon/errors/rpcerrorlist.py` are regenerated from `telethon_generator/data/*.tl` and `data/errors.csv`. Edit the source data, then run `python setup.py gen`. Reason: hand edits are silently overwritten on next regen.
- **Branch strategy:** `v1` is the main integration branch. Feature/security work lives on branches; `security/*` branches carry hardening commits. Merge only with explicit user permission.
- **Security-first posture.** Commits are tagged with finding IDs `C-#` / `H-#` / `M-#` / `L-#` (see `git log --oneline -20`). Preserve this convention when landing security work — the SECURITY_AUDIT_REPORT.md tracks those IDs.
- **H-4 (auth_key plaintext) and H-6 (entity PII plaintext) are CLOSED in Sprint 2 of the MCP bridge work via `mcp_bridge/session/EncryptedSQLiteSession` — see `docs/superflow/specs/2026-04-18-telethon-mcp-bridge-design.md` §2.3.**
- **Be conservative about style.** `flake8` is advisory only (tox env, max-line-length 127, max-complexity 10). Enforced lint is `ruff` in CI. `tests/` is excluded from ruff via `pyproject.toml` — match surrounding code style rather than reformatting.
- **Archived security reference docs live under `docs/security/`** — `lockdown_guide.md` (flagged stale `telethon==1.36.0` pin) and `why_stdio_only.md` (MCP transport reasoning). Canonical runtime posture is `docs/mcp_bridge/README.md` + `SECURITY_AUDIT_REPORT.md`.

## Architecture
Layered, dependency direction: `client/` → `network/` + `sessions/` + `events/` + `_updates/` → `tl/` + `crypto/` + `extensions/` + `errors/` + `helpers`.

- **Entry point:** user constructs `telethon.TelegramClient` (facade in `telethon/client/telegramclient.py`) which mixes in 12 domain modules from `telethon/client/*.py`.
- **Connection bootstrap:** `telethon/client/telegrambaseclient.py` wires `MTProtoSender` + `Connection` + `Session`.
- **Request flow:** `client/*` mixin → `MTProtoSender.send` → `MessagePacker` → `Connection` (transport) → server → receive loop → `MTProtoState` decrypt → dispatch to event builders or return via future.
- **Updates:** Received updates go through `telethon/_updates/messagebox.py` (pts/qts/seq state machine) and `entitycache.py` before event dispatch.
- **Transports live under `telethon/network/connection/`:** abridged, full, intermediate, obfuscated, MTProxy, HTTP — all bounded by `MAX_PACKET_SIZE` (L-4 hardening).
- **Module boundary:** `tl/`, `crypto/`, `sessions/` must NOT import `client/` or `events/`. `crypto/` depending on `tl/` and `errors/` is expected (TL serialization is part of auth).

## Key Files
| File | LOC | Purpose |
|------|-----|---------|
| `telethon/utils.py` | 1571 | Entity parsing, peer resolution, markdown helpers — grab-bag; refactor candidate |
| `telethon/client/messages.py` | 1526 | `send_message` / `edit_message` / `iter_messages` mixin |
| `telethon/client/chats.py` | 1336 | Chat and participant operations mixin |
| `telethon/tl/custom/message.py` | 1244 | Custom Message wrapper (reactions/media/context) |
| `telethon/client/downloads.py` | 1092 | Download orchestration |
| `telethon/client/telegrambaseclient.py` | 980 | Base client, connection + session wiring |
| `telethon/network/mtprotosender.py` | 924 | MTProto request/response engine — critical path |
| `telethon/client/uploads.py` | 874 | Upload orchestration |
| `telethon/_updates/messagebox.py` | 825 | Update state machine |
| `telethon/helpers.py` | 436 | Small crypto/random/string helpers |
| `telethon/errors/rpcerrorlist.py` | 5356 | **GENERATED** — do not edit |
| `telethon/tl/alltlobjects.py` | 2389 | **GENERATED** — do not edit |
| `mcp_bridge/session/encrypted_sqlite.py` | 543 | AES-256-GCM session at rest (closes H-4/H-6) |
| `mcp_bridge/server.py` | ~250 | MCP stdio server entry point |
| `mcp_bridge/tools/poll.py` | 269 | `poll_chat_since` tool — polling-first realtime (notifications spike NO-GO) |
| `mcp_bridge/tools/downloader.py` | 305 | Download tool with safe path join (N-5) |
| `mcp_bridge/tools/bridge.py` | 134 | `send_message` / `ask_user` Q&A bridge |
| `mcp_bridge/rate_limit.py` | 102 | Token-bucket rate limiter for outbound messages |

## Commands
```bash
# Install (uv — primary)
uv sync --frozen --extra dev

# Run tests (CI mirrors this; run locally before pushing)
uv run pytest tests/ -m "not live" --cov=telethon --cov-fail-under=20 -q

# Lint (enforced in CI)
uv run ruff check .

# Audit runtime deps
uv run pip-audit --strict -r requirements.txt

# Regenerate TL layer + error list (edits to telethon_generator/data/*.tl or errors.csv)
python setup.py gen
```

The coverage gate at 20% is intentionally loose — it covers the whole `telethon/` package, most of which is generated TL or untested upstream code. Future work should add per-scope gates (e.g. `--cov=mcp_bridge --cov-fail-under=80`) rather than raise the global floor.

## Conventions (with evidence)
- **Fork layout.** `telethon/` library vs `telethon_generator/` code generator (evidence: top-level `ls` shows both; `setup.py:49-50` defines `GENERATOR_DIR` and `LIBRARY_DIR`).
- **Client is a mixin facade.** 12 modules under `telethon/client/` composed into `TelegramClient` (evidence: `ls telethon/client/` lists account, auth, bots, buttons, chats, dialogs, downloads, messageparse, messages, telegrambaseclient, telegramclient, updates, uploads, users).
- **Security commits tag findings.** `git log --oneline -20` shows `security: ... (L-11, L-12)`, `(M-14)`, `(M-13)` etc. — use this format.
- **cryptography is the default crypto backend** (evidence: `requirements.txt` lists `cryptography>=46,<47`; pyaes is opt-in via `TELETHON_ALLOW_PYAES=1` — see `telethon/crypto/aes.py`).
- **Tests mirror source tree.** `tests/telethon/` mirrors `telethon/` layout; `tests/mcp_bridge/` mirrors `mcp_bridge/` layout.

## Known Issues & Tech Debt
See `docs/superflow/project-health-report.md` for the full table. Highlights:
- `telethon/utils.py:1571` LOC grab-bag — split into peer_utils / markdown_utils / entity_cache
- `telethon/network/mtprotosender.py:924` LOC — refactor cautiously, write tests first
- `telethon/_updates/messagebox.py:825` LOC — high complexity, risky without tests
- Tests: 391 passing across `tests/telethon/` and `tests/mcp_bridge/` (post project-health cleanup). Telethon-side coverage 25.32%; `mcp_bridge/` per-scope coverage 76.19% (CI gate at 74% floor, TODO raise to 80). Most uncovered paths in `telethon/` are upstream code never exercised by this fork.
- ruff is enforced in CI; flake8 still works locally as advisory.
- Smoke-test safety net in place for the five LOC-grab-bag modules (`client/{auth,downloads,uploads}.py`, `network/mtprotosender.py`, `_updates/messagebox.py`) — pins mixin composition + public API signatures so refactor regressions trip a wire.
- The MCP bridge run intentionally chose polling (`mcp_bridge/tools/poll.py`) over push notifications after a spike — see `docs/mcp_bridge/mcp-notifications-spike.md` for the NO-GO rationale.

<!-- updated-by-superflow:2026-04-19 (post project-health cleanup run) -->
<!-- updated-by-superflow:2026-04-19 (post-MCP-bridge merge) -->
