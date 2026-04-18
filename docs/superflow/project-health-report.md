# Project Health Report
<!-- updated-by-superflow:2026-04-17 -->

## Overview

- **Stack:** Python 3.13 async library (asyncio). Dependencies: `pyaes`, `rsa`. Build: `setuptools` via `setup.py` + `pyproject.toml` (tox legacy ini).
- **Framework:** This project **is** a client library — Telethon, a Python MTProto/Telegram client. This repo is a **fork** of [`codeberg.org/Lonami/Telethon`](https://codeberg.org/Lonami/Telethon) at [`github.com/PressAltQQ/Telethon`](https://github.com/PressAltQQ/Telethon).
- **Size:** 247 tracked files; 220 Python source files; ~127k LOC under `telethon/` (of which ~85% is auto-generated TL schema).
- **Tests:** 23 test files vs 150 `telethon/*.py` files (15.3% raw; ~42% when excluding generated TL).
- **Python:** dev 3.13; `setup.py` declares `python_requires=">=3.5"`; tox envs target py35–py38; ReadTheDocs builds on 3.11.
- **Current branch:** `security/fix-medium-and-low-vulnerabilities` — 20 commits ahead of `v1`, all security hardening.

## Hand-Written Large Files (>500 LOC) — Refactoring Candidates

Generated TL files (`telethon/tl/**`, `telethon/errors/rpcerrorlist.py`) are excluded — they should never be refactored by hand.

| File | LOC | Role | Recommendation |
|---|---|---|---|
| `telethon/utils.py` | 1571 | grab-bag utilities (entity parsing, peer resolution, markdown helpers) | Split by concern: peer_utils, markdown_utils, entity_cache |
| `telethon/client/messages.py` | 1526 | `send_message` / `edit_message` / `iter_messages` mixin | Extract query-builder + iterator class |
| `telethon/client/chats.py` | 1336 | chat / participant operations mixin | Extract participant iteration |
| `telethon/tl/custom/message.py` | 1244 | custom Message wrapper | Consider splitting reactions/media/context into traits |
| `telethon/client/downloads.py` | 1092 | download orchestration | Extract downloader strategy from mixin |
| `telethon/client/telegrambaseclient.py` | 980 | base client — connection, session, facade | Extract connection-manager class |
| `telethon/network/mtprotosender.py` | 924 | MTProto request/response engine | Critical path — refactor cautiously, needs tests first |
| `telethon/client/uploads.py` | 874 | upload orchestration | Symmetric refactor with downloads.py |
| `telethon/_updates/messagebox.py` | 825 | update state machine | High complexity; risky to refactor without tests |

## Architecture Violations

**No inverted violations detected.** `tl/`, `crypto/`, and `sessions/` never import `client/` or `events/`. The facade pattern in `TelegramClient` (12 mixins) is by design.

Pragmatic coupling (acceptable for an MTProto library):

| Coupling | Evidence | Severity |
|---|---|---|
| `crypto/` depends on `tl/` and `errors/` | `telethon/crypto/rsa.py:14`, `telethon/crypto/cdndecrypter.py:7-10` | Expected (TL serialization leaks into auth) |
| `network/` depends heavily on `tl/` and `crypto/` | `telethon/network/mtprotosender.py:12,19-29`, `authenticator.py:12-21` | Expected — these ARE the protocol layers |
| Obfuscated transports reach into crypto | `tcpmtproxy.py:13`, `tcpobfuscated.py:6` | Low — protocol requirement |

## Technical Debt (Prioritized)

| Priority | Issue | Location | Evidence | Recommendation |
|---|---|---|---|---|
| P0 | No CI enforcement | — | zero `.github/workflows/`, tox unused in pipeline | Add GH Actions: flake8, pytest, pip-audit |
| P0 | No automated dependency scanning | — | no Dependabot/Renovate/pip-audit | Enable Dependabot + pip-audit in CI |
| P1 | `python_requires=">=3.5"` | `setup.py:232` | 3.5/3.6/3.7/3.8 all EOL | Bump to `>=3.9` minimum |
| P1 | tox envs target py35–py38 | `pyproject.toml:11` | unusable on 3.9+ dev machines | Replace with py39,py310,py311,py312,py313 |
| P1 | `dev-requirements.txt` fully unpinned | `dev-requirements.txt` | `pytest` with no version | Pin to ranges for reproducibility |
| P1 | pyaes unmaintained | `requirements.txt:1` | `pyaes==1.6.1` last release 2017; timing side-channels | Warn or require `cryptg` at runtime |
| P1 | Fork divergence invisible | README.rst, changelog, docs/ | no mention this is a hardening fork | Add CONTRIBUTING.md + README fork header + FORK_NOTES.md |
| P1 | Critical modules untested | `client/{auth,downloads,uploads}.py`, `network/mtprotosender.py`, `_updates/messagebox.py` | no `test_auth.py`, etc. (see Coverage Gaps) | Add smoke tests before refactors |
| P2 | No type hints on public API | `telethon/utils.py` 49 funcs 0 annotated; most `client/*.py` modules | Coverage ~15-20% on public APIs | Progressive typing; start with `client/*.py` public methods |
| P2 | 67 TODO/FIXME comments | top: `mtprotosender.py` (11), `telegrambaseclient.py` (6), `downloads.py` (5), `updates.py` (5) | grep-counted under `telethon/` and `telethon_generator/` | Convert stale TODOs → GitHub issues, delete obsolete |
| P2 | `.gitignore` gaps | — | missing `.env`, `sessions/` (dir form), `.worktrees/`, `.superflow-state.json` | Update .gitignore |
| P2 | Stale linter footprint | — | no ruff/black/isort/mypy; flake8 advisory-only | Add ruff + ruff format; enforce in CI |
| P2 | tox config stale | `pyproject.toml` | still `legacy_tox_ini` with py35–py38 | Modernize (or remove if abandoning tox) |
| P2 | 3 untracked security deliverables at repo root | `01_lockdown_guide.md`, `02_mcp_server.py`, `03_why_stdio_only.md` | numeric prefix suggests drafts | Decide: land in `docs/security/` or delete |
| P3 | `update-docs.sh` uses destructive git | `update-docs.sh` | `git push --force --amend` on gh-pages | Replace with RTD webhook (already configured) |
| P3 | `setup.py pypi` command deprecated build | `setup.py` | uses `setup.py sdist bdist_wheel` | Migrate to `build` + `twine` via GH Actions |

## DevOps & Infrastructure

- **CI/CD:** **None.** No `.github/workflows/`, no GitLab CI, no Travis. Only ReadTheDocs webhook for docs build.
- **Release:** Manual via `python3 setup.py pypi` → twine. Deprecated setup.py build, no tag trigger, no OIDC, no GPG signing.
- **tox:** declared envs py35–py38 (unusable on dev machine at 3.13).
- **Pre-commit:** not configured.
- **Security scanning:** none (Dependabot/Renovate/CodeQL absent).
- **Backups:** N/A (library, not a service).
- **.gitignore gaps:** `.env`, `sessions/` (dir form), `.worktrees/`, `.superflow-state.json` not covered.

## Documentation Freshness

| Doc | Last updated | Status |
|---|---|---|
| `README.rst` | 2023-05-04 | **Stale** — pure upstream Telethon README; no mention of fork, `pip install telethon` resolves to UPSTREAM not this repo |
| `readthedocs/conf.py` | 2022-11-26 | Stale (copyright "2017-2019, Lonami") |
| `readthedocs/misc/changelog.rst` | 2025-11-05 | Covers upstream through v1.42 — **missing fork's security work** (H-/L-/C- fixes) |
| `readthedocs/basic/installation.rst` | 2026-02-28 | Current text, but still references Lonami repo as dev install source |
| `readthedocs/modules/client.rst` | 2019-08-13 | Old but autodoc targets still valid |
| `readthedocs/basic/quick-start.rst` | 2020-10-18 | Likely current |
| `CLAUDE.md` | — | **Missing** (will be created) |
| `llms.txt` | — | **Missing** (will be created) |
| `SECURITY_AUDIT_REPORT.md` | 2026-04-04 | Present (Russian, 38-finding audit) — tracked |
| `01_lockdown_guide.md` | 2026-03-18 | Untracked — recommends `telethon==1.36.0` (repo is at 1.42.0) |
| `03_why_stdio_only.md` | 2026-03-18 | Untracked — general MCP transport reasoning, sound |
| `CONTRIBUTING.md` | — | Missing — no fork-specific policy |

## Security Issues (ALL findings)

### Verified fixed (recent commits)

| Finding | File:Line | Evidence |
|---|---|---|
| L-10 | `setup.py:197-199` | shell=True removed; list form `run(['python3','setup.py','sdist'])`, `run(['twine','upload']+glob('dist/*'))` |
| L-4 | `tcpabridged.py:5,27-29`, `tcpfull.py:8,45-47`, `http.py:5,36-38` | MAX_PACKET_SIZE 2 MiB enforced; raises RuntimeError on overflow |
| L-9/L-13 | `extensions/markdown.py:39`, `events/{callbackquery,newmessage,inlinequery}.py` | length guards against ReDoS (8192 chars / 1000 pattern chars) |
| L-11/L-12 | `network/authenticator.py:87-90`, `utils.py:670` | info leakage reduced in auth error messages |
| H-1 | `network/authenticator.py:29` | `hmac.compare_digest` on DH inner hash |
| H-3 | `network/authenticator.py:51-52,100-144` | asserts → `SecurityError` |
| H-5 | `sessions/sqlite.py:265` | `os.chmod(filename, 0o600)` |
| C-1/C-2 | `network/authenticator.py:138,146` | `_verify_dh_inner_hash`, `_validate_dh_params` |
| H-4 | `mcp_bridge/session/encrypted_sqlite.py` | auth_key encrypted at rest via ChaCha20Poly1305 in EncryptedSQLiteSession — CLOSED Sprint 2 merge |
| H-6 | `mcp_bridge/session/encrypted_sqlite.py` | entity PII (phone, username, name) encrypted at rest via EncryptedSQLiteSession — CLOSED Sprint 2 merge |
| M-14 | `mcp_bridge/session/encrypted_sqlite.py` | session encryption hardening in MCP bridge — CLOSED Sprint 2 merge |
| N-3 | `telethon/crypto/rsa.py:74` | SHA-1 in RSA PKCS#1 per MTProto spec — documented as protocol-level risk, no code fix needed — CLOSED Sprint 2 |
| N-5 | `telethon/client/downloads.py:39` | _safe_join defense-in-depth documented as sanitise-then-confine — CLOSED Sprint 2 |

### Remaining findings

| Severity | ID | Location | Details |
|---|---|---|---|
| MEDIUM | N-1 | `setup.py:232` | `python_requires=">=3.5"` — 3.5/3.6/3.7 EOL, downstream users install on unsupported runtimes. Bump to `>=3.9`. |
| MEDIUM | N-2 | `requirements.txt:1` | `pyaes==1.6.1` — unmaintained since 2017, pure-Python AES vulnerable to timing side-channels. Warn/require `cryptg` at runtime. |
| LOW | M-14-INCOMPLETE | `telethon/network/authenticator.py:231-235` | retry_id "fix" is cosmetic: `retry_id` reassigned then immediately `raise AssertionError` without looping. `AssertionError` vanishes under `python -O` — partially re-introduces H-3 class defect. Implement actual DhGenRetry loop or raise `SecurityError`. |
| INFO | N-6 | `02_mcp_server.py` (untracked) | Well-hardened: stdio-only, channel whitelist, path sanitization, 0o600 perms, rate limiter, size pre-check, cryptg required. Safe. |

**Dependency review:**
- `rsa==4.9` — current, safe (CVE-2020-25658 fixed in 4.7).
- `pyaes==1.6.1` — unmaintained, timing side-channels; mitigated by `cryptg` extras.
- `cryptg` — unpinned in `extras_require`.
- No hardcoded credentials (README `api_hash` is a documented placeholder).

### Coverage gaps (major untested modules)

`client/auth.py` (677 LOC), `client/downloads.py` (1092), `client/uploads.py` (874), `client/chats.py` (1336), `client/telegrambaseclient.py` (980), `network/mtprotosender.py` (924), `_updates/messagebox.py` (825), `tl/custom/message.py` (1244), `tl/custom/conversation.py` (529).

## Overall Posture

Strong recovery from an initial security audit: C-1, C-2 and all H-# findings are fixed or closed. H-4 and H-6 (session encryption) are CLOSED via Sprint 2 MCP bridge work (`mcp_bridge/session/EncryptedSQLiteSession`). Low/medium fixes are minimal-change and accurate. The two residual concerns worth tackling next: (1) `M-14` retry is cosmetic; (2) `pyaes` is structurally weak and `python_requires` is stuck at 3.5.

Beyond security, the project's main non-code weaknesses are **no CI**, **no dependency scanning**, **stale tox envs**, and **zero fork-divergence signaling** to downstream readers.

<!-- updated-by-superflow:2026-04-18 Sprint 2 merge -->
