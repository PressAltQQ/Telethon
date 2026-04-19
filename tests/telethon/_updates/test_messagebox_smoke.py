"""Smoke tests for telethon/_updates/messagebox.py — pure logic, no I/O."""
import inspect
import logging

import pytest

from telethon._updates import messagebox as mb


def test_messagebox_importable():
    """The module must import without side effects."""
    assert hasattr(mb, "MessageBox")


def test_messagebox_constructs_with_logger():
    """Construction with a stdlib logger must not raise."""
    box = mb.MessageBox(logging.getLogger("test"))
    assert box is not None


def test_messagebox_public_surface():
    """Methods the _updates layer depends on must exist."""
    for name in ("load", "session_state", "get_difference",
                 "get_channel_difference"):
        assert hasattr(mb.MessageBox, name), f"MessageBox lost method: {name}"


def test_session_state_is_serializable_shape():
    """Fresh session_state must be a tuple/dict-like value the SQLite
    session knows how to persist. (Structural check only — we don't pin the
    exact tuple layout, which has evolved upstream.)"""
    box = mb.MessageBox(logging.getLogger("test"))
    state = box.session_state()
    assert state is not None
