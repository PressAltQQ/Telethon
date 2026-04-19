# Project Health Report
<!-- updated-by-superflow:2026-04-19 (post project-health cleanup run) -->

## Overview

- **Stack:** Python 3.11+ async library (asyncio). Runtime deps: `cryptography`, `keyring`, `mcp`, `rsa`. Build: `setuptools` via `setup.py` + `pyproject.toml`. Package manager: `uv` (lock at `uv.lock`).
- **Framework:** This project **is** a client library — Telethon, a Python MTProto/Telegram client. This repo is a **fork** of [`codeberg.org/Lonami/Telethon`](https://codeberg.org/Lonami/Telethon) at [`github.com/PressAltQQ/Telethon`](https://github.com/PressAltQQ/Telethon).
- **Size:** ~250 tracked files; 220+ Python source files; ~127k LOC under `telethon/` (of which ~85% is auto-generated TL schema). New `mcp_bridge/` subtree (~1.5k LOC) shipped 2026-04-19.
- **Tests:** 391 passing (`tests/telethon/` + `tests/mcp_bridge/`). Telethon-side line coverage 25.32%; `mcp_bridge/` per-scope coverage 76.19% (CI gate at 74% floor, TODO raise to 80).
- **Python:** dev 3.13; CI matrix py3.11 + py3.13; `python_requires=">=3.11"`.
- **CI:** `.github/workflows/ci.yml` — ruff, pytest with two coverage gates (global telethon 20% floor + per-scope mcp_bridge 74% floor), pip-audit on every push/PR.
- **Active branch:** `v1` is the integration branch (5 MCP bridge sprints + project-health cleanup landed 2026-04-19).

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
| ✅ DONE | No CI enforcement | — | resolved — `.github/workflows/ci.yml` runs ruff + pytest + pip-audit on py3.11/3.13 | Sprint 1 of MCP bridge run |
| ✅ DONE | No automated dependency scanning | — | resolved — `.github/dependabot.yml` (security-only, weekly) + pip-audit in CI | Sprint 1 |
| ✅ DONE | `python_requires=">=3.5"` | `setup.py` | resolved — `python_requires=">=3.11"` | Sprint 1 |
| ✅ DONE | tox envs target py35–py38 | `pyproject.toml` | tox legacy ini removed; uv + pytest is canonical now | Sprint 1 |
| ✅ DONE | pyaes unmaintained | `requirements.txt` | pyaes removed from runtime path; `cryptography` is default; pyaes opt-in via `TELETHON_ALLOW_PYAES=1` | Sprint 2 |
| ✅ DONE | Stale linter footprint | — | ruff added + enforced in CI | Sprint 1 |
| ✅ DONE | `update-docs.sh` destructive git | `update-docs.sh` | file deleted | Sprint 1 |
| ✅ DONE | `dev-requirements.txt` fully unpinned | `dev-requirements.txt` | file removed in project-health cleanup Sprint A; canonical dev deps live in `pyproject.toml [project.optional-dependencies].dev`; install via `uv sync --extra dev` | Sprint A (PR #11) |
| P1 | Fork divergence invisible | README.rst, changelog, docs/ | no mention this is a hardening fork | Add CONTRIBUTING.md + README fork header + FORK_NOTES.md (still pending — Bundle B, deferred from project-health cleanup run) |
| ✅ DONE | Critical modules untested | `client/{auth,downloads,uploads}.py`, `network/mtprotosender.py`, `_updates/messagebox.py` | 25 smoke tests added in project-health cleanup Sprint C pinning mixin composition + public-API kwargs + coroutine/sync-ness; tests 366 → 391; API drifts discovered and annotated (is_user_authorized in `users.py`, MTProtoSender.send synchronous, loggers mandatory) | Sprint C (PR #12) |
| ✅ DONE | Coverage gate is loose (20%) | `.github/workflows/ci.yml` | added per-scope `--cov=mcp_bridge --cov-fail-under=74` step (floor = current - 2; `# TODO: raise to 80` inline) while keeping global telethon floor at 20% — avoids raising the loose global for mostly-upstream code | Sprint A (PR #11) |
| P2 | No type hints on public API | `telethon/utils.py` 49 funcs 0 annotated; most `client/*.py` modules | Coverage ~15-20% on public APIs | Progressive typing; start with `client/*.py` public methods |
| P2 | 67 TODO/FIXME comments | top: `mtprotosender.py` (11), `telegrambaseclient.py` (6), `downloads.py` (5), `updates.py` (5) | grep-counted under `telethon/` and `telethon_generator/` | Convert stale TODOs → GitHub issues, delete obsolete |
| ✅ DONE | `.gitignore` gaps | — | added `.env`/`.env.*`, `/sessions/` dir-form, `.superflow-state.json`; whitelisted `docs/security/` and `docs/superpowers/`; added `!docs/` parent re-inclusion (empirical fix — git's "can't re-include if parent is ignored" rule) | Sprint A (PR #11) |
| ✅ DONE | 3 untracked security deliverables at repo root | `01_lockdown_guide.md`, `02_mcp_server.py`, `03_why_stdio_only.md` | `02_mcp_server.py` deleted (superseded by `mcp_bridge/`); `01` and `03` archived under `docs/security/` with `ARCHIVED 2026-04-19` headers (lockdown_guide flags stale `telethon==1.36.0` pin) | Sprint A (PR #11) |
| P3 | `setup.py pypi` command deprecated build | `setup.py` | uses `setup.py sdist bdist_wheel` | Migrate to `build` + `twine` via GH Actions |

## DevOps & Infrastructure

- **CI/CD:** GitHub Actions `.github/workflows/ci.yml` — Python 3.11 + 3.13 matrix; ruff lint, pytest with two coverage gates (global `--cov=telethon --cov-fail-under=20` + per-scope `--cov=mcp_bridge --cov-fail-under=74`), pip-audit on runtime deps. Actions pinned to commit SHAs.
- **Release:** still manual via `python3 setup.py pypi` → twine. No tag trigger, no OIDC, no GPG signing (P3 follow-up).
- **Dependency scanning:** Dependabot (`.github/dependabot.yml`, security-only, weekly) + pip-audit in CI.
- **Pre-commit:** not configured (low priority — ruff in CI catches lint).
- **.gitignore gaps:** CLOSED in project-health cleanup Sprint A — `.env`/`.env.*`, `/sessions/` dir-form, `.superflow-state.json` now ignored; `.worktrees/` was already ignored.

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

All previously remaining findings are now closed via the MCP bridge sprint work:

| Severity | ID | Location | Status |
|---|---|---|---|
| MEDIUM | N-1 | `setup.py` | CLOSED Sprint 1 — `python_requires=">=3.11"` |
| MEDIUM | N-2 | `requirements.txt` | CLOSED Sprint 2 — pyaes removed from runtime path; `TELETHON_ALLOW_PYAES` gate |
| INFO | N-4 | `setup.py` | CLOSED Sprint 1 — `python_requires` bumped from `>=3.5` to `>=3.11` |
| LOW | M-14-INCOMPLETE | `telethon/network/authenticator.py` | CLOSED Sprint 2 — real DhGenRetry loop, `SecurityError` |
| INFO | N-6 | `02_mcp_server.py` (untracked) | CLOSED Sprint 3 — logic migrated into `mcp_bridge/`; file deleted |

## Success Criteria Status (SC1–SC6)

| ID | Criterion | Status | Evidence |
|---|---|---|---|
| SC1 | Downloader works end-to-end; `auth_key` stays in bridge, never in MCP response | **CLOSED** Sprint 3 | Payload isolation test in `tests/mcp_bridge/tools/test_poll.py::TestPayloadIsolation`; grep-style auth_key assertion in `test_downloader.py` |
| SC2 | Stealing session file is insufficient to log in | **CLOSED** Sprint 2 | `EncryptedSQLiteSession` requires OS keyring entry; negative test in `tests/mcp_bridge/session/test_keystore.py` |
| SC3 | Realtime bridge latency < 2 s p95 | **CLOSED** Sprint 5 | `tests/mcp_bridge/test_realtime_latency.py` — N=200, p95 = 0.7 ms (well under 2000 ms) |
| SC4 | CI green (pytest, ruff, pip-audit) | **CLOSED** Sprint 1 | `.github/workflows/ci.yml` matrix Python 3.11 + 3.13 |
| SC5 | All prior audit findings closed or deferred with rationale | **CLOSED** Sprint 5 | All H/M/N findings resolved; table above. Deferred items documented in spec §12. |
| SC6 | Reproducible install < 15 min | **CLOSED** Sprint 5 | `docs/mcp_bridge/README.md` runbook (steps 1–6 + optional launchd) |

**Dependency review:**
- `rsa==4.9` — current, safe (CVE-2020-25658 fixed in 4.7).
- `pyaes==1.6.1` — unmaintained, timing side-channels; mitigated by `cryptg` extras.
- `cryptg` — unpinned in `extras_require`.
- No hardcoded credentials (README `api_hash` is a documented placeholder).

### Coverage gaps (major untested modules)

`client/auth.py` (677 LOC), `client/downloads.py` (1092), `client/uploads.py` (874), `client/chats.py` (1336), `client/telegrambaseclient.py` (980), `network/mtprotosender.py` (924), `_updates/messagebox.py` (825), `tl/custom/message.py` (1244), `tl/custom/conversation.py` (529).

## Overall Posture

Strong recovery from the initial security audit, now reinforced by the MCP bridge run (2026-04-19) and the subsequent project-health cleanup run (same day): all C/H/M/L/N findings are fixed or have a documented residual rationale. CI is live with ruff + pytest (two coverage gates: global telethon 20% + per-scope mcp_bridge 74%) + pip-audit on every push, runtime crypto is on `cryptography` instead of `pyaes`, and session storage is encrypted at rest via `mcp_bridge/session/EncryptedSQLiteSession`. The five critical untested modules (`client/{auth,downloads,uploads}.py`, `network/mtprotosender.py`, `_updates/messagebox.py`) now have dedicated smoke-test files that pin their public surface against accidental refactor regressions.

Remaining non-code weaknesses, in rough priority:

1. **Fork divergence signaling** — README.rst still reads as upstream; no `CONTRIBUTING.md` or fork header. A reader doing `pip install telethon` lands on upstream, not this fork. (Deferred as Bundle B from the project-health cleanup run — warrants its own plan because it requires product-level framing decisions about how loudly to signal the fork.)
2. **No type hints on public API** (P2) — progressive typing starting with `client/*.py` public methods.
3. **67 TODO/FIXME comments** (P2) — convert stale TODOs to GitHub issues or delete.
4. **Release tooling is deprecated** (P3) — `setup.py pypi` → twine; no tag trigger, OIDC, or GPG signing. Migrate to `build` + `twine` via GH Actions.

<!-- updated-by-superflow:2026-04-19 project-health cleanup run complete — Sprint A (hygiene, PR #11) + Sprint C (smoke tests, PR #12) merged into v1 -->
<!-- updated-by-superflow:2026-04-19 Phase 3 complete — MCP bridge merged into v1 -->
