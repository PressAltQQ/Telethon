"""Read-only allow-list: every name must resolve to a real Telethon class."""
from __future__ import annotations

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
        # I1: destructive — must be absent
        "auth.LogOutRequest",
        # I2: hijack vector — must be absent
        "auth.ExportLoginTokenRequest",
    }
    overlap = forbidden & READONLY_ALLOWLIST
    assert not overlap, f"Forbidden RPCs found in allow-list: {overlap}"


def test_ping_request_resolves_to_real_class():
    """C1: PingRequest (bare name, no sub-module) must resolve and be allow-listed."""
    cls = resolve_class("PingRequest")
    assert isinstance(cls, type)
    assert "PingRequest" in READONLY_ALLOWLIST


def test_ping_delay_disconnect_request_resolves_to_real_class():
    """C1: PingDelayDisconnectRequest must resolve and be allow-listed."""
    cls = resolve_class("PingDelayDisconnectRequest")
    assert isinstance(cls, type)
    assert "PingDelayDisconnectRequest" in READONLY_ALLOWLIST


def test_resolve_class_bare_name_no_submodule():
    """C1: resolve_class must handle bare names (no dot) from telethon.tl.functions."""
    from telethon.tl.functions import PingRequest as ExpectedClass
    assert resolve_class("PingRequest") is ExpectedClass
