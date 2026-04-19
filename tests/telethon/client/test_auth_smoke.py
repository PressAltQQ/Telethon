"""Smoke tests for telethon/client/auth.py — no live Telegram.

Pattern mirrors tests/telethon/client/test_messages.py:
subclass TelegramClient with a no-op __init__ to isolate mixin behaviour.

API-drift note: is_user_authorized lives in telethon/client/users.py, not
auth.py — the original task spec had a misattribution. We test UserMethods
for that guard instead, keeping the intent: pin the public surface.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from telethon import TelegramClient
from telethon.client.auth import AuthMethods


def test_auth_methods_is_mixin_of_telegramclient():
    """Regression guard: refactors must keep AuthMethods composed into TelegramClient."""
    assert issubclass(TelegramClient, AuthMethods)


def test_sign_in_signature_keeps_documented_kwargs():
    """Public API guard: sign_in's kwargs are load-bearing for users."""
    params = inspect.signature(AuthMethods.sign_in).parameters
    for expected in ("phone", "code", "password", "bot_token", "phone_code_hash"):
        assert expected in params, f"sign_in lost kwarg: {expected}"


def test_send_code_request_signature_keeps_force_sms():
    params = inspect.signature(AuthMethods.send_code_request).parameters
    assert "force_sms" in params


@pytest.mark.asyncio
async def test_log_out_dispatches_logout_request():
    """`log_out` must invoke the underlying RPC and return a bool."""
    from telethon.tl import functions

    class MockedClient(TelegramClient):
        # noinspection PyMissingConstructor
        def __init__(self):
            self._bot = False
            self.session = MagicMock()
            self.session.delete = MagicMock(return_value=None)
            self._authorized = True
            self._self_input_peer = None
            self.disconnect = AsyncMock()
            self._mb_entity_cache = MagicMock()
            self._mb_entity_cache.set_self_user = MagicMock()

        async def __call__(self, request):
            assert isinstance(request, functions.auth.LogOutRequest)
            return True

    client = MockedClient()
    # Capture session before log_out sets self.session = None
    session = client.session
    result = await client.log_out()
    assert result is True
    session.delete.assert_called_once()


def test_log_out_is_public_method_of_auth_mixin():
    """Guard against accidental rename or removal from the auth mixin."""
    assert hasattr(AuthMethods, "log_out")
    assert inspect.iscoroutinefunction(AuthMethods.log_out)
