"""Smoke tests for telethon/network/mtprotosender.py — no network I/O.

Construct MTProtoSender, assert initial state invariants, verify the
class surface exposes the documented send/disconnect/connect methods.
Mirrors the defensive-coding style of test_mtprotostate.py.

API-drift note: MTProtoSender.__init__ requires `loggers` (no default).
We supply a minimal defaultdict-style dict that returns a stdlib logger.
"""
import inspect
import logging

import pytest

from telethon.network.mtprotosender import MTProtoSender


def _make_loggers():
    """Minimal loggers mapping compatible with MTProtoSender.__init__."""
    import collections
    base = logging.getLogger("test.mtprotosender")

    class _Loggers(dict):
        def __missing__(self, key):
            return base.getChild(key.split(".")[-1])

    return _Loggers()


def test_mtprotosender_constructs_disconnected():
    """New sender must start disconnected with an empty pending set."""
    sender = MTProtoSender(auth_key=None, loggers=_make_loggers())
    # The sender exposes _user_connected internally; keep the
    # assertion tolerant — we only need "not currently connected".
    assert getattr(sender, "_user_connected", False) is False
    # Pending-state containers should exist and be empty-ish on construction.
    assert hasattr(sender, "_pending_state")
    assert len(sender._pending_state) == 0


def test_mtprotosender_has_public_surface():
    """Public methods the rest of the codebase relies on must exist."""
    for name in ("connect", "disconnect", "send", "is_connected"):
        assert hasattr(MTProtoSender, name), f"MTProtoSender lost method: {name}"


def test_send_is_synchronous_queue_push():
    """send() is a synchronous method that enqueues requests (not async).

    API-drift note: send() puts requests into an async queue; it is NOT a
    coroutine itself. Pinning this to catch an accidental signature change.
    """
    assert not inspect.iscoroutinefunction(MTProtoSender.send)
    assert callable(MTProtoSender.send)


def test_connect_is_coroutine_function():
    assert inspect.iscoroutinefunction(MTProtoSender.connect)


def test_disconnect_is_coroutine_function():
    assert inspect.iscoroutinefunction(MTProtoSender.disconnect)


def test_init_accepts_loggers_option():
    """Regression guard: init signature accepts loggers kwarg used by client."""
    params = inspect.signature(MTProtoSender.__init__).parameters
    assert "loggers" in params
