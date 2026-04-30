# MCP Bridge Read-Only Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a runtime-selectable read-only mode to the MCP bridge that strips write/communication tools and enforces a fail-closed allow-list on outbound MTProto requests.

**Architecture:** Three layers in `mcp_bridge/` only — (1) thin entrypoint `server_readonly.py` that sets `MCP_READONLY=1`; (2) tool-registration filter in `server.py` skips `bridge.py`; (3) `readonly_guard.install(client)` wraps `MTProtoSender.send` against `readonly_allowlist.READONLY_ALLOWLIST` (frozenset of TL class names). Rate-limiter from `rate_limit.py` is rebound to the download tool. `telethon/` is not touched.

**Tech Stack:** Python 3.11+, asyncio, pytest, Telethon (in-tree fork), MCP SDK.

**Spec:** `docs/superflow/specs/2026-04-30-mcp-readonly-mode-design.md`

---

## File Structure

| File | Status | Purpose |
|------|--------|---------|
| `mcp_bridge/readonly_allowlist.py` | Create | Frozenset of allow-listed TL request class names + import-time validation |
| `mcp_bridge/readonly_guard.py` | Create | `install(client)` wraps `client._sender.send`; checks single+batch requests |
| `mcp_bridge/server_readonly.py` | Create | Thin entrypoint: sets `MCP_READONLY=1`, calls `__main__.main()` |
| `mcp_bridge/server.py` | Modify | In `run_server()`: skip `bridge.py` imports/registrations when `MCP_READONLY=1` |
| `mcp_bridge/__main__.py` | Modify | Call `readonly_guard.install(client)` after `client_holder.start()` when `MCP_READONLY=1` |
| `mcp_bridge/tools/downloader.py` | Modify | Acquire rate-limiter token at start of `download_file` when `MCP_READONLY=1` |
| `tests/mcp_bridge/test_readonly_allowlist.py` | Create | Import-time validation: every name resolves to real Telethon class |
| `tests/mcp_bridge/test_readonly_guard.py` | Create | Allow/block/batch tests on the wrapped sender |
| `tests/mcp_bridge/test_readonly_server.py` | Create | Tool registry filter; download rate-limit gate |
| `tests/mcp_bridge/test_readonly_entrypoint.py` | Create | Smoke: `python -m mcp_bridge.server_readonly --help` exits 0 |

---

## Task 1: Allow-list module with import validation

**Files:**
- Create: `mcp_bridge/readonly_allowlist.py`
- Test: `tests/mcp_bridge/test_readonly_allowlist.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/mcp_bridge/test_readonly_allowlist.py`:

```python
"""Read-only allow-list: every name must resolve to a real Telethon class."""
from __future__ import annotations

import importlib

import pytest

from mcp_bridge.readonly_allowlist import (
    READONLY_ALLOWLIST,
    resolve_class,
)


def test_allowlist_is_non_empty_frozenset():
    assert isinstance(READONLY_ALLOWLIST, frozenset)
    assert len(READONLY_ALLOWLIST) > 30  # sanity: we listed 40-ish classes


def test_every_entry_resolves_to_real_class():
    """Fail-loud at import time: typos and upstream renames are caught here."""
    unresolved = []
    for fq_name in READONLY_ALLOWLIST:
        try:
            cls = resolve_class(fq_name)
        except Exception as exc:
            unresolved.append((fq_name, str(exc)))
            continue
        assert isinstance(cls, type), f"{fq_name} did not resolve to a class"
    assert not unresolved, f"Unresolved allow-list entries: {unresolved}"


def test_known_write_methods_not_in_allowlist():
    """Regression guard: write RPCs must never be allow-listed."""
    forbidden = {
        "messages.SendMessageRequest",
        "messages.EditMessageRequest",
        "messages.DeleteMessagesRequest",
        "messages.ForwardMessagesRequest",
        "messages.SendMediaRequest",
        "messages.SendReactionRequest",
        "messages.SaveDraftRequest",
        "channels.JoinChannelRequest",
        "channels.LeaveChannelRequest",
        "account.UpdateProfileRequest",
        "account.ResetAuthorizationRequest",
        "auth.AcceptLoginTokenRequest",
    }
    overlap = forbidden & READONLY_ALLOWLIST
    assert not overlap, f"Forbidden RPCs found in allow-list: {overlap}"
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `uv run pytest tests/mcp_bridge/test_readonly_allowlist.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_bridge.readonly_allowlist'`

- [ ] **Step 1.3: Create the allow-list module**

Create `mcp_bridge/readonly_allowlist.py`:

```python
"""
Read-only mode allow-list of permitted MTProto TL request classes.

Names are stored as ``module.ClassName`` (e.g. ``messages.GetHistoryRequest``)
where ``module`` is the sub-module under ``telethon.tl.functions``. Anything
not in this set is blocked by ``readonly_guard``.

Curated, fail-closed. To add a new read RPC, add the fully qualified name
here and add a test in test_readonly_allowlist.py if it requires special
handling.
"""
from __future__ import annotations

import importlib
from typing import Type


READONLY_ALLOWLIST: frozenset[str] = frozenset({
    # Auth / login (login allowed; AcceptLoginToken explicitly NOT included)
    "auth.SendCodeRequest",
    "auth.ResendCodeRequest",
    "auth.SignInRequest",
    "auth.SignUpRequest",
    "auth.CheckPasswordRequest",
    "auth.LogOutRequest",
    "auth.ImportLoginTokenRequest",
    "auth.ExportLoginTokenRequest",
    "auth.ExportAuthorizationRequest",
    "auth.ImportAuthorizationRequest",
    "account.GetPasswordRequest",

    # Bootstrap / housekeeping
    "help.GetConfigRequest",
    "help.GetNearestDcRequest",
    "updates.GetStateRequest",
    "updates.GetDifferenceRequest",
    "updates.GetChannelDifferenceRequest",

    # Reading messages and chats
    "messages.GetHistoryRequest",
    "messages.GetMessagesRequest",
    "messages.SearchRequest",
    "messages.SearchGlobalRequest",
    "messages.GetDialogsRequest",
    "messages.GetPeerDialogsRequest",
    "messages.GetStickerSetRequest",
    "channels.GetMessagesRequest",
    "channels.GetFullChannelRequest",
    "channels.GetChannelsRequest",
    "channels.GetParticipantRequest",
    "channels.GetParticipantsRequest",

    # Entity resolution
    "contacts.ResolveUsernameRequest",
    "contacts.GetContactsRequest",
    "users.GetUsersRequest",
    "users.GetFullUserRequest",
    "photos.GetUserPhotosRequest",

    # Self-introspection (read-only)
    "account.GetAuthorizationsRequest",

    # Downloads
    "upload.GetFileRequest",
    "upload.GetCdnFileRequest",
    "upload.ReuploadCdnFileRequest",
    "upload.GetCdnFileHashesRequest",
    "upload.GetWebFileRequest",
})


def resolve_class(fq_name: str) -> Type:
    """Resolve ``module.ClassName`` to the actual class under telethon.tl.functions.

    Raises ImportError or AttributeError if the class doesn't exist.
    """
    if "." not in fq_name:
        raise ValueError(f"Expected 'module.ClassName', got {fq_name!r}")
    module_part, class_name = fq_name.rsplit(".", 1)
    module = importlib.import_module(f"telethon.tl.functions.{module_part}")
    return getattr(module, class_name)


# Pre-computed set of bare class names (last component) for the hot path in
# readonly_guard. Comparing ``type(req).__name__`` against strings is cheaper
# than reflecting back to fully qualified names on every RPC.
ALLOWED_CLASS_NAMES: frozenset[str] = frozenset(
    name.rsplit(".", 1)[1] for name in READONLY_ALLOWLIST
)
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `uv run pytest tests/mcp_bridge/test_readonly_allowlist.py -v`
Expected: PASS — 3 tests pass.

- [ ] **Step 1.5: Commit**

```bash
git add mcp_bridge/readonly_allowlist.py tests/mcp_bridge/test_readonly_allowlist.py
git commit -m "feat(mcp_bridge): readonly_allowlist with import-time validation"
```

---

## Task 2: RPC guard — single request

**Files:**
- Create: `mcp_bridge/readonly_guard.py`
- Test: `tests/mcp_bridge/test_readonly_guard.py`

- [ ] **Step 2.1: Write the failing test**

Create `tests/mcp_bridge/test_readonly_guard.py`:

```python
"""Tests for readonly_guard: wraps MTProtoSender.send and enforces allow-list."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mcp_bridge.readonly_guard import install


class _FakeSendMessage:
    """Stand-in for telethon.tl.functions.messages.SendMessageRequest."""
    pass
_FakeSendMessage.__name__ = "SendMessageRequest"


class _FakeGetHistory:
    pass
_FakeGetHistory.__name__ = "GetHistoryRequest"


def _make_client():
    """Client with a fake _sender that records calls."""
    sender = MagicMock()
    sender.send.return_value = "OK"  # not a real Future, fine for test
    client = SimpleNamespace(_sender=sender)
    return client, sender


class TestSingleRequest:
    def test_allow_listed_request_passes_through(self):
        client, sender = _make_client()
        install(client)
        result = client._sender.send(_FakeGetHistory())
        assert result == "OK"
        sender.send.assert_called_once()

    def test_blocked_request_raises_permission_error(self):
        client, sender = _make_client()
        install(client)
        with pytest.raises(PermissionError, match="SendMessageRequest"):
            client._sender.send(_FakeSendMessage())
        sender.send.assert_not_called()

    def test_blocked_request_logs_warning(self, caplog):
        client, sender = _make_client()
        install(client)
        with caplog.at_level(logging.WARNING, logger="mcp_bridge.readonly_guard"):
            with pytest.raises(PermissionError):
                client._sender.send(_FakeSendMessage())
        assert any("SendMessageRequest" in rec.message for rec in caplog.records)
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `uv run pytest tests/mcp_bridge/test_readonly_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_bridge.readonly_guard'`

- [ ] **Step 2.3: Implement the guard (single-request path only)**

Create `mcp_bridge/readonly_guard.py`:

```python
"""
Read-only RPC guard.

`install(client)` replaces ``client._sender.send`` with a wrapper that
checks each TLRequest against the allow-list. Anything not allow-listed
raises PermissionError; the original send is never called for that batch.

Fail-closed: unknown class name => block.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from mcp_bridge.readonly_allowlist import ALLOWED_CLASS_NAMES

__log__ = logging.getLogger(__name__)


def _name(request: Any) -> str:
    return type(request).__name__


def _check_one(request: Any) -> None:
    name = _name(request)
    if name not in ALLOWED_CLASS_NAMES:
        __log__.warning("readonly_guard blocked: %s", name)
        raise PermissionError(f"RPC blocked in read-only mode: {name}")


def install(client) -> None:
    """Wrap ``client._sender.send`` with the allow-list checker.

    Idempotent: a second call on the same client is a no-op.
    """
    sender = client._sender
    if getattr(sender, "_readonly_guard_installed", False):
        return

    original_send = sender.send

    def guarded_send(request, ordered: bool = False):
        _check_one(request)
        return original_send(request, ordered=ordered)

    sender.send = guarded_send
    sender._readonly_guard_installed = True
    __log__.info("readonly_guard installed; allow-list size=%d", len(ALLOWED_CLASS_NAMES))
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `uv run pytest tests/mcp_bridge/test_readonly_guard.py -v`
Expected: PASS — 3 tests pass.

- [ ] **Step 2.5: Commit**

```bash
git add mcp_bridge/readonly_guard.py tests/mcp_bridge/test_readonly_guard.py
git commit -m "feat(mcp_bridge): readonly_guard single-request allow-list check"
```

---

## Task 3: RPC guard — batch handling

**Files:**
- Modify: `mcp_bridge/readonly_guard.py`
- Modify: `tests/mcp_bridge/test_readonly_guard.py`

- [ ] **Step 3.1: Add the failing batch tests**

Append to `tests/mcp_bridge/test_readonly_guard.py`:

```python
class TestBatchRequest:
    def test_all_allowed_batch_passes(self):
        client, sender = _make_client()
        install(client)
        client._sender.send([_FakeGetHistory(), _FakeGetHistory()])
        sender.send.assert_called_once()

    def test_one_blocked_in_batch_blocks_entire_batch(self):
        client, sender = _make_client()
        install(client)
        with pytest.raises(PermissionError, match="SendMessageRequest"):
            client._sender.send([_FakeGetHistory(), _FakeSendMessage()])
        sender.send.assert_not_called()

    def test_idempotent_install(self):
        client, sender = _make_client()
        install(client)
        first_wrapped = client._sender.send
        install(client)
        assert client._sender.send is first_wrapped
```

- [ ] **Step 3.2: Run tests to verify the batch ones fail**

Run: `uv run pytest tests/mcp_bridge/test_readonly_guard.py::TestBatchRequest -v`
Expected: FAIL — `test_one_blocked_in_batch_blocks_entire_batch` fails because current `_check_one` doesn't iterate.

- [ ] **Step 3.3: Update guard to handle batches**

Replace the body of `guarded_send` in `mcp_bridge/readonly_guard.py`. Find:

```python
    def guarded_send(request, ordered: bool = False):
        _check_one(request)
        return original_send(request, ordered=ordered)
```

Replace with:

```python
    def guarded_send(request, ordered: bool = False):
        # Telethon batches via list/tuple. is_list_like in telethon.utils
        # accepts list/tuple/generator; we mirror list/tuple here (sender.send
        # never sees generators in practice).
        if isinstance(request, (list, tuple)):
            for item in request:
                _check_one(item)
        else:
            _check_one(request)
        return original_send(request, ordered=ordered)
```

- [ ] **Step 3.4: Run all guard tests to verify they pass**

Run: `uv run pytest tests/mcp_bridge/test_readonly_guard.py -v`
Expected: PASS — 6 tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add mcp_bridge/readonly_guard.py tests/mcp_bridge/test_readonly_guard.py
git commit -m "feat(mcp_bridge): readonly_guard batch-aware blocking"
```

---

## Task 4: Tool registry filter in server.py

**Files:**
- Modify: `mcp_bridge/server.py:108-152`
- Test: `tests/mcp_bridge/test_readonly_server.py`

- [ ] **Step 4.1: Write the failing tests**

Create `tests/mcp_bridge/test_readonly_server.py`:

```python
"""Tests for read-only mode wiring inside server.run_server."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mcp_bridge.server as server_mod


def _stub_config():
    return SimpleNamespace(max_ops_per_minute=60, burst=5)


def _patched_run(monkeypatch):
    """Run run_server() far enough to register tools, then bail out.

    We stub stdio_server so the loop never starts; we capture the registry by
    inspecting server_mod._TOOL_REGISTRY after the registrations execute.
    """
    server_mod._TOOL_REGISTRY.clear()

    class _StubStdioCM:
        async def __aenter__(self):
            # Raise to break out of run_server before it actually serves.
            raise RuntimeError("__stop_after_register__")
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "mcp.server.stdio.stdio_server", lambda: _StubStdioCM(),
    )


@pytest.mark.asyncio
async def test_full_mode_registers_send_message_and_ask_user(monkeypatch):
    monkeypatch.delenv("MCP_READONLY", raising=False)
    _patched_run(monkeypatch)
    client = MagicMock()
    config = _stub_config()
    with pytest.raises(RuntimeError, match="__stop_after_register__"):
        await server_mod.run_server(client, config, correlation=MagicMock())
    assert "send_message" in server_mod._TOOL_REGISTRY
    assert "ask_user" in server_mod._TOOL_REGISTRY
    assert "poll_chat_since" in server_mod._TOOL_REGISTRY
    assert "download_file" in server_mod._TOOL_REGISTRY


@pytest.mark.asyncio
async def test_readonly_mode_omits_send_message_and_ask_user(monkeypatch):
    monkeypatch.setenv("MCP_READONLY", "1")
    _patched_run(monkeypatch)
    client = MagicMock()
    config = _stub_config()
    with pytest.raises(RuntimeError, match="__stop_after_register__"):
        await server_mod.run_server(client, config, correlation=MagicMock())
    assert "send_message" not in server_mod._TOOL_REGISTRY
    assert "ask_user" not in server_mod._TOOL_REGISTRY
    assert "poll_chat_since" in server_mod._TOOL_REGISTRY
    assert "download_file" in server_mod._TOOL_REGISTRY
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `uv run pytest tests/mcp_bridge/test_readonly_server.py -v`
Expected: FAIL — `test_readonly_mode_omits_send_message_and_ask_user` fails because both tools are always registered.

- [ ] **Step 4.3: Modify `mcp_bridge/server.py`**

In `run_server` (currently lines 108-152), find:

```python
    from mcp_bridge.tools.bridge import ask_user as _ask_user
    from mcp_bridge.tools.bridge import send_message as _send_message
    from mcp_bridge.tools.downloader import download_file, list_channel_files
    from mcp_bridge.tools.poll import poll_chat_since as _poll_chat_since
```

Replace with:

```python
    readonly = os.environ.get("MCP_READONLY") == "1"

    from mcp_bridge.tools.downloader import download_file, list_channel_files
    from mcp_bridge.tools.poll import poll_chat_since as _poll_chat_since
    if not readonly:
        from mcp_bridge.tools.bridge import ask_user as _ask_user
        from mcp_bridge.tools.bridge import send_message as _send_message
```

Then find the block that registers `send_message` and `ask_user`:

```python
    register_tool("send_message", lambda **kw: _send_message(
        client, config, kw["chat_id"], kw["text"]
    ))

    async def _ask_user_handler(**kw):
        return await _ask_user(
            client=client,
            correlation=correlation,
            config=config,
            chat_id=kw["chat_id"],
            text=kw["text"],
            timeout_sec=kw.get("timeout_sec", 1800),
            target_user_id=kw.get("target_user_id"),
        )

    register_tool("ask_user", _ask_user_handler)
```

Wrap it in `if not readonly:`:

```python
    if not readonly:
        register_tool("send_message", lambda **kw: _send_message(
            client, config, kw["chat_id"], kw["text"]
        ))

        async def _ask_user_handler(**kw):
            return await _ask_user(
                client=client,
                correlation=correlation,
                config=config,
                chat_id=kw["chat_id"],
                text=kw["text"],
                timeout_sec=kw.get("timeout_sec", 1800),
                target_user_id=kw.get("target_user_id"),
            )

        register_tool("ask_user", _ask_user_handler)
    else:
        __log__.info("MCP_READONLY=1 — skipping send_message/ask_user registration")
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `uv run pytest tests/mcp_bridge/test_readonly_server.py -v`
Expected: PASS — 2 tests pass.

- [ ] **Step 4.5: Run the full mcp_bridge test suite to check nothing regressed**

Run: `uv run pytest tests/mcp_bridge/ -v`
Expected: PASS — all tests pass.

- [ ] **Step 4.6: Commit**

```bash
git add mcp_bridge/server.py tests/mcp_bridge/test_readonly_server.py
git commit -m "feat(mcp_bridge): MCP_READONLY filter for send_message/ask_user"
```

---

## Task 5: Apply rate limiter to download_file in read-only mode

**Files:**
- Modify: `mcp_bridge/tools/downloader.py:170-200`
- Modify: `tests/mcp_bridge/test_readonly_server.py`

- [ ] **Step 5.1: Add the failing test**

Append to `tests/mcp_bridge/test_readonly_server.py`:

```python
class TestDownloadRateLimit:
    @pytest.mark.asyncio
    async def test_readonly_acquires_token_before_download(self, monkeypatch):
        """In read-only mode, download_file must call rate_limiter.acquire()."""
        monkeypatch.setenv("MCP_READONLY", "1")

        from mcp_bridge import rate_limit

        rate_limit.reset_rate_limiter()
        bucket = MagicMock()
        bucket.acquire = AsyncMock()
        monkeypatch.setattr(
            "mcp_bridge.rate_limit.get_rate_limiter", lambda config: bucket
        )

        from mcp_bridge.tools import downloader

        # Force whitelist + early-exit path: not_whitelisted is the cheapest
        # branch to verify the acquire() was called BEFORE the whitelist check.
        config = SimpleNamespace(
            channels=[],
            max_ops_per_minute=60,
            burst=5,
        )

        # We expect NotWhitelistedError, but acquire() must have been called first.
        from mcp_bridge.errors import NotWhitelistedError
        with pytest.raises(NotWhitelistedError):
            await downloader.download_file(MagicMock(), config, channel_id=999, message_id=1)
        bucket.acquire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_full_mode_does_not_acquire_token_for_download(self, monkeypatch):
        monkeypatch.delenv("MCP_READONLY", raising=False)

        from mcp_bridge import rate_limit
        rate_limit.reset_rate_limiter()
        bucket = MagicMock()
        bucket.acquire = AsyncMock()
        monkeypatch.setattr(
            "mcp_bridge.rate_limit.get_rate_limiter", lambda config: bucket
        )

        from mcp_bridge.tools import downloader
        config = SimpleNamespace(channels=[], max_ops_per_minute=60, burst=5)
        from mcp_bridge.errors import NotWhitelistedError
        with pytest.raises(NotWhitelistedError):
            await downloader.download_file(MagicMock(), config, channel_id=999, message_id=1)
        bucket.acquire.assert_not_called()
```

- [ ] **Step 5.2: Run the new tests to verify they fail**

Run: `uv run pytest tests/mcp_bridge/test_readonly_server.py::TestDownloadRateLimit -v`
Expected: FAIL — `test_readonly_acquires_token_before_download` fails (no acquire happens).

- [ ] **Step 5.3: Modify `mcp_bridge/tools/downloader.py`**

At top of file, after existing imports, add:

```python
import os
```

Then in `download_file` (line 170), find the function body just after the docstring and BEFORE `if not is_whitelisted(...)`:

```python
    Returns {local_path, already_downloaded, size?, sha256?}.
    """
    if not is_whitelisted(channel_id, "channels", config):
```

Insert the rate-limit gate:

```python
    Returns {local_path, already_downloaded, size?, sha256?}.
    """
    if os.environ.get("MCP_READONLY") == "1":
        from mcp_bridge.rate_limit import get_rate_limiter
        await get_rate_limiter(config).acquire()

    if not is_whitelisted(channel_id, "channels", config):
```

- [ ] **Step 5.4: Run the new tests to verify they pass**

Run: `uv run pytest tests/mcp_bridge/test_readonly_server.py::TestDownloadRateLimit -v`
Expected: PASS — 2 tests pass.

- [ ] **Step 5.5: Run the full downloader test suite**

Run: `uv run pytest tests/mcp_bridge/tools/ tests/mcp_bridge/test_readonly_server.py -v`
Expected: PASS — no regressions.

- [ ] **Step 5.6: Commit**

```bash
git add mcp_bridge/tools/downloader.py tests/mcp_bridge/test_readonly_server.py
git commit -m "feat(mcp_bridge): rate-limit downloads in read-only mode"
```

---

## Task 6: Wire guard install in `__main__._run`

**Files:**
- Modify: `mcp_bridge/__main__.py:152-156`
- Test: extend existing `tests/mcp_bridge/test_main_wiring.py`

- [ ] **Step 6.1: Add the failing test**

Append to `tests/mcp_bridge/test_main_wiring.py` (the existing helpers `make_args`, `make_config` are reused):

```python
class TestReadonlyGuardWiring:
    def test_readonly_calls_guard_install(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_READONLY", "1")
        args = make_args()
        config = make_config(tmp_path)

        with patch("mcp_bridge.config.load_config", return_value=config), \
             patch("mcp_bridge.logging_setup.setup_logging"), \
             patch("mcp_bridge.client_holder.start", new=AsyncMock()), \
             patch("mcp_bridge.client_holder.client", return_value=MagicMock()) as m_client, \
             patch("mcp_bridge.client_holder.stop", new=AsyncMock()), \
             patch("mcp_bridge.server.run_server", new=AsyncMock()), \
             patch("mcp_bridge.correlation.Correlation") as m_corr, \
             patch("mcp_bridge.readonly_guard.install") as m_install:
            m_corr.return_value = MagicMock()
            from mcp_bridge.__main__ import _run
            asyncio.run(_run(args))
            m_install.assert_called_once_with(m_client.return_value)

    def test_full_mode_does_not_call_guard_install(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MCP_READONLY", raising=False)
        args = make_args()
        config = make_config(tmp_path)

        with patch("mcp_bridge.config.load_config", return_value=config), \
             patch("mcp_bridge.logging_setup.setup_logging"), \
             patch("mcp_bridge.client_holder.start", new=AsyncMock()), \
             patch("mcp_bridge.client_holder.client", return_value=MagicMock()), \
             patch("mcp_bridge.client_holder.stop", new=AsyncMock()), \
             patch("mcp_bridge.server.run_server", new=AsyncMock()), \
             patch("mcp_bridge.correlation.Correlation") as m_corr, \
             patch("mcp_bridge.readonly_guard.install") as m_install:
            m_corr.return_value = MagicMock()
            from mcp_bridge.__main__ import _run
            asyncio.run(_run(args))
            m_install.assert_not_called()
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `uv run pytest tests/mcp_bridge/test_main_wiring.py::TestReadonlyGuardWiring -v`
Expected: FAIL — `test_readonly_calls_guard_install` fails (install never called).

- [ ] **Step 6.3: Modify `mcp_bridge/__main__.py`**

In `_run`, find the block (around line 152):

```python
    # 4. Run server
    try:
        from mcp_bridge.server import run_server
        await run_server(client_holder.client(), config, correlation=correlation)
        return 0
```

Replace with:

```python
    # 4. Run server
    try:
        if os.environ.get("MCP_READONLY") == "1":
            from mcp_bridge.readonly_guard import install as _install_guard
            _install_guard(client_holder.client())
        from mcp_bridge.server import run_server
        await run_server(client_holder.client(), config, correlation=correlation)
        return 0
```

Add `import os` at the top of the file if not already present (it isn't).

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `uv run pytest tests/mcp_bridge/test_main_wiring.py -v`
Expected: PASS — including the two new tests and the existing wiring tests.

- [ ] **Step 6.5: Commit**

```bash
git add mcp_bridge/__main__.py tests/mcp_bridge/test_main_wiring.py
git commit -m "feat(mcp_bridge): install readonly_guard in __main__ when MCP_READONLY=1"
```

---

## Task 7: Read-only entrypoint module

**Files:**
- Create: `mcp_bridge/server_readonly.py`
- Test: `tests/mcp_bridge/test_readonly_entrypoint.py`

- [ ] **Step 7.1: Write the failing tests**

Create `tests/mcp_bridge/test_readonly_entrypoint.py`:

```python
"""Smoke tests for the read-only entrypoint."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


def test_module_sets_env_var_on_import():
    """Importing the module must set MCP_READONLY=1 even before main() runs."""
    code = (
        "import os, sys\n"
        "os.environ.pop('MCP_READONLY', None)\n"
        "import mcp_bridge.server_readonly  # noqa: F401\n"
        "print(os.environ.get('MCP_READONLY'))\n"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
    assert out == "1"


def test_help_exits_zero():
    """`python -m mcp_bridge.server_readonly --help` exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_bridge.server_readonly", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert "Telethon MCP Bridge" in proc.stdout
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `uv run pytest tests/mcp_bridge/test_readonly_entrypoint.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 7.3: Create `mcp_bridge/server_readonly.py`**

```python
"""
Read-only entrypoint for the MCP bridge.

Sets ``MCP_READONLY=1`` before delegating to the normal main(). Provides a
misuse-resistant launch path: a systemd/launchd unit that invokes
``python -m mcp_bridge.server_readonly`` cannot accidentally start the full
read/write server even if the env var is missing from the unit file.
"""
from __future__ import annotations

import os

os.environ.setdefault("MCP_READONLY", "1")

from mcp_bridge.__main__ import main


if __name__ == "__main__":
    main()
```

- [ ] **Step 7.4: Run tests to verify they pass**

Run: `uv run pytest tests/mcp_bridge/test_readonly_entrypoint.py -v`
Expected: PASS — 2 tests pass.

- [ ] **Step 7.5: Commit**

```bash
git add mcp_bridge/server_readonly.py tests/mcp_bridge/test_readonly_entrypoint.py
git commit -m "feat(mcp_bridge): server_readonly entrypoint module"
```

---

## Task 8: Final integration + lint pass

- [ ] **Step 8.1: Run the full test suite**

Run: `uv run pytest tests/ -m "not live" --cov=mcp_bridge --cov-fail-under=20 -q`
Expected: PASS, no regressions, coverage ≥ 20%.

- [ ] **Step 8.2: Run lint**

Run: `uv run ruff check mcp_bridge/ tests/mcp_bridge/`
Expected: PASS, no warnings.

- [ ] **Step 8.3: Manual smoke check (optional, requires authorized session)**

Document only — do not include in CI:

```bash
# In one terminal, with an existing authorized session:
python -m mcp_bridge.server_readonly --config <path>

# In another terminal, send an MCP list-tools request via stdio.
# Expected: tool list contains list_channel_files, download_file,
# poll_chat_since; does NOT contain send_message, ask_user.
```

- [ ] **Step 8.4: Final commit (only if any fixes were needed)**

```bash
git add -A
git commit -m "chore(mcp_bridge): readonly mode lint/test polish"
```

---

## Self-Review (completed)

- **Spec coverage:** Every spec section maps to a task — §1 architecture/3 data flow → tasks 4+6+7; §2.1 entrypoint → task 7; §2.2 tool filter → task 4; §2.3 allow-list → task 1; §2.4 guard → tasks 2+3; §4 download rate-limit → task 5; §5 error handling → covered by guard tests (task 2); §6 testing → tasks 1, 2, 3, 4, 5, 6, 7, 8; §7 out-of-scope → not implemented (correct); §8 migration — no extra task needed (no schema/dep changes).
- **Placeholder scan:** No TBD/TODO/"add error handling" markers; every code step has actual code.
- **Type consistency:** `install(client)` signature matches between definition (task 2) and call site (task 6); `READONLY_ALLOWLIST` and `ALLOWED_CLASS_NAMES` names consistent across tasks 1–3; env var spelled `MCP_READONLY` everywhere.
