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


class TestExportedSenderWrapping:
    @pytest.mark.asyncio
    async def test_exported_sender_send_is_wrapped(self):
        """C2: _create_exported_sender must return a sender with guard installed."""
        exported_sender = MagicMock()
        exported_sender.send.return_value = "EX_OK"

        original_create = MagicMock(return_value=exported_sender)

        import asyncio
        async def _async_create(*args, **kwargs):
            return original_create(*args, **kwargs)

        client, _ = _make_client()
        client._create_exported_sender = _async_create

        install(client)

        result_sender = await client._create_exported_sender(2)
        # The returned sender's .send should block write requests
        with pytest.raises(ReadOnlyBlockedError, match="SendMessageRequest"):
            result_sender.send(_FakeSendMessage())

    @pytest.mark.asyncio
    async def test_exported_sender_allows_listed_request(self):
        """C2: exported sender must still pass allowed requests through."""
        exported_sender = MagicMock()
        exported_sender.send.return_value = "EX_OK"

        async def _async_create(*args, **kwargs):
            return exported_sender

        client, _ = _make_client()
        client._create_exported_sender = _async_create

        install(client)

        result_sender = await client._create_exported_sender(2)
        result = result_sender.send(_FakeGetHistory())
        assert result == "EX_OK"

    @pytest.mark.asyncio
    async def test_exported_sender_idempotent_wrap(self):
        """C2: re-wrapping an already-guarded exported sender is a no-op."""
        exported_sender = MagicMock()
        exported_sender.send.return_value = "EX_OK"

        async def _async_create(*args, **kwargs):
            return exported_sender

        client, _ = _make_client()
        client._create_exported_sender = _async_create

        install(client)

        sender1 = await client._create_exported_sender(2)
        first_send = sender1.send
        # Calling again should return same sender object; wrap should be idempotent
        sender2 = await client._create_exported_sender(2)
        assert sender2.send is first_send

    def test_kwargs_forwarded_to_original_send(self):
        """M2: guarded_send must forward *args/**kwargs to original send."""
        client, sender = _make_client()
        # Capture the original mock before install replaces sender.send
        original_mock = sender.send
        install(client)
        req = _FakeGetHistory()
        client._sender.send(req, ordered=True)
        # original_mock is the MagicMock; verify ordered=True was forwarded
        _, call_kwargs = original_mock.call_args
        assert call_kwargs.get("ordered") is True
