"""Tests for readonly_guard: wraps MTProtoSender.send and enforces allow-list."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mcp_bridge.errors import ReadOnlyBlockedError
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
        original_send = sender.send
        install(client)
        result = client._sender.send(_FakeGetHistory())
        assert result == "OK"
        original_send.assert_called_once()

    def test_blocked_request_raises_readonly_blocked_error(self):
        client, sender = _make_client()
        original_send = sender.send
        install(client)
        with pytest.raises(ReadOnlyBlockedError, match="SendMessageRequest"):
            client._sender.send(_FakeSendMessage())
        original_send.assert_not_called()

    def test_blocked_request_logs_warning(self, caplog):
        client, sender = _make_client()
        install(client)
        with caplog.at_level(logging.WARNING, logger="mcp_bridge.readonly_guard"):
            with pytest.raises(ReadOnlyBlockedError):
                client._sender.send(_FakeSendMessage())
        assert any("SendMessageRequest" in rec.message for rec in caplog.records)


class TestBatchRequest:
    def test_all_allowed_batch_passes(self):
        client, sender = _make_client()
        original_send = sender.send
        install(client)
        client._sender.send([_FakeGetHistory(), _FakeGetHistory()])
        original_send.assert_called_once()

    def test_one_blocked_in_batch_blocks_entire_batch(self):
        client, sender = _make_client()
        original_send = sender.send
        install(client)
        with pytest.raises(ReadOnlyBlockedError, match="SendMessageRequest"):
            client._sender.send([_FakeGetHistory(), _FakeSendMessage()])
        original_send.assert_not_called()

    def test_idempotent_install(self):
        client, sender = _make_client()
        install(client)
        first_wrapped = client._sender.send
        install(client)
        assert client._sender.send is first_wrapped
