"""
Read-only RPC guard.

`install(client)` replaces ``client._sender.send`` with a wrapper that
checks each TLRequest against the allow-list. Anything not allow-listed
raises ReadOnlyBlockedError; the original send is never called for that
request.

Also wraps ``client._create_exported_sender`` (async) so that borrowed
cross-DC senders used by download_media / FILE_MIGRATE_X are guarded too.

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


def _wrap_sender(sender) -> None:
    """Install the guard on *sender*.send in-place. Idempotent."""
    if sender.__dict__.get("_readonly_guard_installed", False):
        return

    original_send = sender.send

    def guarded_send(request, *args, **kwargs):
        # Telethon batches via list/tuple. is_list_like in telethon.utils
        # accepts list/tuple/generator; we mirror list/tuple here (sender.send
        # never sees generators in practice).
        if isinstance(request, (list, tuple)):
            for item in request:
                _check_one(item)
        else:
            _check_one(request)
        return original_send(request, *args, **kwargs)

    sender.send = guarded_send
    sender._readonly_guard_installed = True


def install(client) -> None:
    """Wrap ``client._sender.send`` with the allow-list checker.

    Also patches ``client._create_exported_sender`` if present so that
    borrowed cross-DC senders (used for FILE_MIGRATE_X downloads) are
    guarded before being returned to callers.

    Idempotent: a second call on the same client is a no-op.
    """
    _wrap_sender(client._sender)
    __log__.info("readonly_guard installed on primary sender; allow-list size=%d", len(ALLOWED_CLASS_NAMES))

    # Patch _create_exported_sender if the client has it (TelegramBaseClient does).
    original_create = getattr(client, "_create_exported_sender", None)
    if original_create is not None and not getattr(client, "_readonly_guard_create_patched", False):
        async def _guarded_create(*args, **kwargs):
            sender = await original_create(*args, **kwargs)
            _wrap_sender(sender)
            return sender

        client._create_exported_sender = _guarded_create
        client._readonly_guard_create_patched = True
        __log__.info("readonly_guard patched _create_exported_sender")
