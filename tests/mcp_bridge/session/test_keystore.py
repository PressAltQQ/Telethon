"""
Tests for mcp_bridge.session.keystore.

We mock the keyring and environment variables so no real OS keyring is needed.
"""
import base64
import os
from unittest.mock import MagicMock, patch, call

import pytest

from mcp_bridge.session.keystore import (
    ConfigInvalidError,
    KeyringUnavailableError,
    load_or_create,
    KEYRING_SERVICE,
)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class TestLoadFromKeyring:
    def test_keyring_returns_both_key_and_salt(self):
        """When keyring has both key and salt, load_or_create returns them."""
        key = os.urandom(32)
        salt = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=True):
            # Remove any existing SESSION_KEY/SALT from env.
            for var in ('TELETHON_SESSION_KEY', 'TELETHON_SESSION_SALT'):
                os.environ.pop(var, None)

            mock_kr.get_password.side_effect = lambda svc, account: {
                f"myses:key": _b64(key),
                f"myses:salt": _b64(salt),
            }.get(account)

            result_key, result_salt = load_or_create("myses")

        assert result_key == key
        assert result_salt == salt

    def test_keyring_partial_key_only_raises_config_invalid(self):
        """Keyring has key but no salt → ConfigInvalidError."""
        key = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)

            mock_kr.get_password.side_effect = lambda svc, account: (
                _b64(key) if account == "myses:key" else None
            )

            with pytest.raises(ConfigInvalidError):
                load_or_create("myses")

    def test_keyring_partial_salt_only_raises_config_invalid(self):
        """Keyring has salt but no key → ConfigInvalidError."""
        salt = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)

            mock_kr.get_password.side_effect = lambda svc, account: (
                _b64(salt) if account == "myses:salt" else None
            )

            with pytest.raises(ConfigInvalidError):
                load_or_create("myses")


class TestEnvVarPriority:
    def test_env_var_used_when_keyring_empty(self):
        """When keyring is empty but env vars are set, use env vars."""
        key = os.urandom(32)
        salt = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {
                 'TELETHON_SESSION_KEY': _b64(key),
                 'TELETHON_SESSION_SALT': _b64(salt),
             }, clear=False):
            mock_kr.get_password.return_value = None  # empty keyring

            result_key, result_salt = load_or_create("ses")

        assert result_key == key
        assert result_salt == salt

    def test_env_var_partial_key_only_raises(self):
        """Only TELETHON_SESSION_KEY set (no salt) → ConfigInvalidError."""
        key = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {'TELETHON_SESSION_KEY': _b64(key)}, clear=False):
            os.environ.pop('TELETHON_SESSION_SALT', None)
            mock_kr.get_password.return_value = None

            with pytest.raises(ConfigInvalidError):
                load_or_create("ses")

    def test_env_var_partial_salt_only_raises(self):
        """Only TELETHON_SESSION_SALT set (no key) → ConfigInvalidError."""
        salt = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {'TELETHON_SESSION_SALT': _b64(salt)}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            mock_kr.get_password.return_value = None

            with pytest.raises(ConfigInvalidError):
                load_or_create("ses")


class TestMixedSourceMismatch:
    def test_keyring_and_env_both_present_but_different_raises(self):
        """If both keyring and env have material but they differ → ConfigInvalidError."""
        keyring_key = os.urandom(32)
        keyring_salt = os.urandom(32)
        env_key = os.urandom(32)  # different
        env_salt = os.urandom(32)  # different

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {
                 'TELETHON_SESSION_KEY': _b64(env_key),
                 'TELETHON_SESSION_SALT': _b64(env_salt),
             }, clear=False):
            mock_kr.get_password.side_effect = lambda svc, account: {
                "ses:key": _b64(keyring_key),
                "ses:salt": _b64(keyring_salt),
            }.get(account)

            with pytest.raises(ConfigInvalidError, match="differ"):
                load_or_create("ses")

    def test_keyring_and_env_same_values_accepted(self):
        """If both sources agree on the same key/salt, no error is raised."""
        key = os.urandom(32)
        salt = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {
                 'TELETHON_SESSION_KEY': _b64(key),
                 'TELETHON_SESSION_SALT': _b64(salt),
             }, clear=False):
            mock_kr.get_password.side_effect = lambda svc, account: {
                "ses:key": _b64(key),
                "ses:salt": _b64(salt),
            }.get(account)

            result_key, result_salt = load_or_create("ses")

        assert result_key == key
        assert result_salt == salt


class TestFirstRun:
    def test_first_run_generates_and_stores_key_and_salt(self):
        """If neither keyring nor env has material, generate new key+salt and store."""
        stored = {}

        def fake_get(svc, account):
            return stored.get(account)

        def fake_set(svc, account, value):
            stored[account] = value

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)
            mock_kr.get_password.side_effect = fake_get
            mock_kr.set_password.side_effect = fake_set

            key, salt = load_or_create("newses")

        assert isinstance(key, bytes)
        assert len(key) == 32
        assert isinstance(salt, bytes)
        assert len(salt) == 32

        # Verify both were stored in keyring.
        assert "newses:key" in stored
        assert "newses:salt" in stored
        assert base64.b64decode(stored["newses:key"]) == key
        assert base64.b64decode(stored["newses:salt"]) == salt

    def test_two_successive_calls_return_same_key(self):
        """Second call (with keyring now populated) returns the same key+salt."""
        stored = {}

        def fake_get(svc, account):
            return stored.get(account)

        def fake_set(svc, account, value):
            stored[account] = value

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)
            mock_kr.get_password.side_effect = fake_get
            mock_kr.set_password.side_effect = fake_set

            key1, salt1 = load_or_create("ses2")
            key2, salt2 = load_or_create("ses2")

        assert key1 == key2
        assert salt1 == salt2


class TestKeyringUnavailable:
    def test_keyring_error_and_no_env_raises_keyring_unavailable(self):
        """If keyring raises and env vars are absent, KeyringUnavailableError is raised."""
        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)
            mock_kr.get_password.side_effect = RuntimeError("keyring backend failed")

            with pytest.raises(KeyringUnavailableError):
                load_or_create("ses")

    def test_keyring_error_but_env_vars_present_succeeds(self):
        """If keyring raises but env vars are set, fall back to env vars."""
        key = os.urandom(32)
        salt = os.urandom(32)

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {
                 'TELETHON_SESSION_KEY': _b64(key),
                 'TELETHON_SESSION_SALT': _b64(salt),
             }, clear=False):
            mock_kr.get_password.side_effect = RuntimeError("keyring backend failed")

            result_key, result_salt = load_or_create("ses")

        assert result_key == key
        assert result_salt == salt
