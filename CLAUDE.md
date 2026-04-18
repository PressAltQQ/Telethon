# Telethon (PressAltQQ fork) — Claude Instructions

## Project Overview
Security-hardened fork of Telethon, a pure-Python asyncio MTProto/Telegram client. Upstream: `codeberg.org/Lonami/Telethon`. This fork: `github.com/PressAltQQ/Telethon`. The fork exists to harden MTProto handling and diverges from upstream **only by security posture** — no feature divergence.

Stack: Python 3.13 (dev), asyncio. Runtime deps `pyaes==1.6.1`, `rsa==4.9` (pinned as M-11). Optional: `cryptg`, `python-socks[asyncio]`, `hachoir`, `pillow`, `isal`. Build: `setup.py` + `pyproject.toml` (legacy tox ini). No CI.

Framework claims are verified against imports (`pyaes` in `telethon/crypto/aes.py:9` and `aesctr.py:4`; `asyncio` in `telethon/client/telegrambaseclient.py:4`).

## Key Rules
- **Never edit generated TL code by hand.** `telethon/tl/**` and `telethon/errors/rpcerrorlist.py` are regenerated from `telethon_generator/data/*.tl` and `data/errors.csv`. Edit the source data, then run `python setup.py gen`. Reason: hand edits are silently overwritten on next regen.
- **Branch strategy:** `v1` is the main integration branch. Feature/security work lives on branches; `security/*` branches carry hardening commits. Merge only with explicit user permission.
- **Security-first posture.** Commits are tagged with finding IDs `C-#` / `H-#` / `M-#` / `L-#` (see `git log --oneline -20`). Preserve this convention when landing security work — the SECURITY_AUDIT_REPORT.md tracks those IDs.
- **H-4 (auth_key plaintext) and H-6 (entity PII plaintext) are CLOSED in Sprint 2 of the MCP bridge work via `mcp_bridge/session/EncryptedSQLiteSession` — see `docs/superflow/specs/2026-04-18-telethon-mcp-bridge-design.md` §2.3.**
- **Be conservative about style.** `flake8` is advisory only (tox env, max-line-length 127, max-complexity 10). No enforced black/ruff — match surrounding code style rather than reformatting.
- **Do not touch untracked security deliverables** (`01_lockdown_guide.md`, `02_mcp_server.py`, `03_why_stdio_only.md`) unless explicitly asked.

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

## Commands
```bash
# Install for dev
pip install -r requirements.txt -r optional-requirements.txt -r dev-requirements.txt

# Regenerate TL layer + error list (edits to telethon_generator/data/*.tl or errors.csv)
python setup.py gen

# Run tests (no CI — mandatory locally before commit)
pytest tests/

# Advisory lint (matches tox flake env)
flake8 telethon/ telethon_generator/ tests/ \
  --exclude telethon/tl/,telethon/errors/rpcerrorlist.py \
  --max-complexity=10 --max-line-length=127

# Full tox run (untested in this environment)
tox -e py
tox -e flake
```

## Conventions (with evidence)
- **Fork layout.** `telethon/` library vs `telethon_generator/` code generator (evidence: top-level `ls` shows both; `setup.py:49-50` defines `GENERATOR_DIR` and `LIBRARY_DIR`).
- **Client is a mixin facade.** 12 modules under `telethon/client/` composed into `TelegramClient` (evidence: `ls telethon/client/` lists account, auth, bots, buttons, chats, dialogs, downloads, messageparse, messages, telegrambaseclient, telegramclient, updates, uploads, users).
- **Security commits tag findings.** `git log --oneline -20` shows `security: ... (L-11, L-12)`, `(M-14)`, `(M-13)` etc. — use this format.
- **pyaes + rsa are the only crypto runtime deps** (evidence: `requirements.txt` lists `pyaes==1.6.1` and `rsa==4.9`; imports confirmed in `telethon/crypto/aes.py:9`, `telethon/crypto/aesctr.py:4`).
- **Tests mirror source tree.** `tests/telethon/` mirrors `telethon/` layout (evidence: `ls tests/telethon/` shows client, crypto, events, extensions, network, sessions, tl).

## Known Issues & Tech Debt
See `docs/superflow/project-health-report.md` for the full table. Highlights:
- `telethon/utils.py:1571` LOC grab-bag — split into peer_utils / markdown_utils / entity_cache
- `telethon/network/mtprotosender.py:924` LOC — refactor cautiously, write tests first
- `telethon/_updates/messagebox.py:825` LOC — high complexity, risky without tests
- Tests: 23 pytest files vs 150 hand-written source files (~15% raw, ~42% excluding generated TL). No CI — `pytest tests/` must be run locally.
- No enforced formatter; flake8 is advisory only.

<!-- updated-by-superflow:2026-04-18 -->
