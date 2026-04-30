"""
Read-only RPC guard.

`install(client)` replaces ``client._sender.send`` with a wrapper that
checks each TLRequest against the allow-list. Anything not allow-listed
raises PermissionError; the original send is never called for that request.

Fail-closed: unknown class name => block.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp_bridge.errors import ReadOnlyBlockedError
from mcp_bridge.readonly_allowlist import ALLOWED_CLASS_NAMES

__log__ = logging.getLogger(__name__)


def _name(request: Any) -> str:
    return type(request).__name__


def _check_one(request: Any) -> None:
    name = _name(request)
    if name not in ALLOWED_CLASS_NAMES:
        __log__.warning("readonly_guard blocked: %s", name)
        raise ReadOnlyBlockedError(f"RPC blocked in read-only mode: {name}")


def install(client) -> None:
    """Wrap ``client._sender.send`` with the allow-list checker.

    Idempotent: a second call on the same client is a no-op.
    """
    sender = client._sender
    if sender.__dict__.get("_readonly_guard_installed", False):
        return

    original_send = sender.send

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

    sender.send = guarded_send
    sender._readonly_guard_installed = True
    __log__.info("readonly_guard installed; allow-list size=%d", len(ALLOWED_CLASS_NAMES))
