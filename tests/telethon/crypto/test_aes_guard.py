"""
Tests for the TELETHON_ALLOW_PYAES guard in telethon/crypto/aes.py.

These tests verify that:
1. With TELETHON_ALLOW_PYAES=1, pyaes fallback works and emits a WARNING.
2. Without TELETHON_ALLOW_PYAES (and without cryptg), first encryption
   call raises ImportError with the cryptg-required message.
3. With cryptg available (mocked), pyaes is NOT imported.
"""
import importlib
import logging
import os
import sys
import types
import unittest.mock


def _reload_aes_module(env_overrides: dict) -> types.ModuleType:
    """Reload telethon.crypto.aes with specific environment variables and
    without cryptg so we exercise the software path."""
    # Remove cached modules so we start fresh.
    for key in list(sys.modules.keys()):
        if key in ('telethon.crypto.aes', 'telethon.crypto', 'telethon.crypto.libssl',
                   'pyaes'):
            # We'll let libssl stay but reset aes-specific state
            pass

    with unittest.mock.patch.dict(os.environ, env_overrides, clear=False):
        # Force reload of the aes module so module-level code re-runs.
        if 'telethon.crypto.aes' in sys.modules:
            del sys.modules['telethon.crypto.aes']

        import telethon.crypto.aes as aes_mod
        # Reset the warning flag so tests are independent.
        aes_mod._pyaes_warning_emitted = False
        return aes_mod


class TestAesGuardWithPyaesAllowed:
    """When TELETHON_ALLOW_PYAES=1 and cryptg is absent, pyaes fallback works."""

    def test_encrypt_succeeds_with_env_var(self, caplog):
        """encrypt_ige works with TELETHON_ALLOW_PYAES=1 (pyaes fallback)."""
        import telethon.crypto.aes as aes_mod

        # Ensure we are in the pyaes-allowed test environment (conftest sets it).
        assert os.environ.get("TELETHON_ALLOW_PYAES") == "1"

        # Patch cryptg to None to force the pyaes path.
        with unittest.mock.patch.object(aes_mod, 'cryptg', None), \
             unittest.mock.patch.object(aes_mod, 'libssl') as mock_libssl:
            mock_libssl.encrypt_ige = None
            mock_libssl.decrypt_ige = None
            aes_mod._pyaes_warning_emitted = False

            key = os.urandom(32)
            iv = os.urandom(32)
            plain = b'\x00' * 16

            with caplog.at_level(logging.WARNING, logger='telethon.crypto.aes'):
                result = aes_mod.AES.encrypt_ige(plain, key, iv)

            assert isinstance(result, bytes)
            assert len(result) == 16
            assert "pyaes fallback active" in caplog.text

    def test_warning_emitted_only_once(self, caplog):
        """The pyaes fallback WARNING is emitted on the first call only."""
        import telethon.crypto.aes as aes_mod

        with unittest.mock.patch.object(aes_mod, 'cryptg', None), \
             unittest.mock.patch.object(aes_mod, 'libssl') as mock_libssl:
            mock_libssl.encrypt_ige = None
            mock_libssl.decrypt_ige = None
            aes_mod._pyaes_warning_emitted = False

            key = os.urandom(32)
            iv = os.urandom(32)
            plain = b'\x00' * 16

            with caplog.at_level(logging.WARNING, logger='telethon.crypto.aes'):
                aes_mod.AES.encrypt_ige(plain, key, iv)
                first_warning_count = caplog.text.count("pyaes fallback active")
                aes_mod.AES.encrypt_ige(plain, key, iv)
                second_warning_count = caplog.text.count("pyaes fallback active")

            # WARNING appears exactly once even after two calls.
            assert first_warning_count == 1
            assert second_warning_count == 1


class TestAesGuardWithoutPyaesAllowed:
    """When TELETHON_ALLOW_PYAES is unset and cryptg is absent, ImportError is raised."""

    def test_import_does_not_raise(self):
        """import telethon.crypto.aes should NOT raise even without pyaes env var.
        The guard fires only on the first encryption call, not at import time."""
        # This is already proved by the module being importable in the test suite,
        # but let's be explicit: removing TELETHON_ALLOW_PYAES must not break import.
        import telethon.crypto.aes  # noqa: F401 (just verifying no exception)
        assert True  # import completed without error

    def test_encrypt_raises_without_env_var(self):
        """Without TELETHON_ALLOW_PYAES and without cryptg, encryption raises ImportError."""
        import telethon.crypto.aes as aes_mod

        with unittest.mock.patch.dict(os.environ, {}, clear=False) as env, \
             unittest.mock.patch.object(aes_mod, 'cryptg', None), \
             unittest.mock.patch.object(aes_mod, 'libssl') as mock_libssl:
            # Remove TELETHON_ALLOW_PYAES for this test.
            env.pop("TELETHON_ALLOW_PYAES", None)
            mock_libssl.encrypt_ige = None
            mock_libssl.decrypt_ige = None
            aes_mod._pyaes_warning_emitted = False

            key = os.urandom(32)
            iv = os.urandom(32)
            plain = b'\x00' * 16

            try:
                aes_mod.AES.encrypt_ige(plain, key, iv)
                assert False, "Expected ImportError was not raised"
            except ImportError as exc:
                assert "cryptg is required for runtime use" in str(exc)


class TestAesGuardWithCryptg:
    """When cryptg is available (mocked), pyaes is never called."""

    def test_cryptg_path_used_when_available(self):
        """encrypt_ige delegates to cryptg and does NOT call _get_software_aes."""
        import telethon.crypto.aes as aes_mod

        mock_cryptg = unittest.mock.MagicMock()
        expected = b'\xab' * 16
        mock_cryptg.encrypt_ige.return_value = expected

        with unittest.mock.patch.object(aes_mod, 'cryptg', mock_cryptg):
            key = os.urandom(32)
            iv = os.urandom(32)
            plain = b'\x00' * 16

            result = aes_mod.AES.encrypt_ige(plain, key, iv)

        mock_cryptg.encrypt_ige.assert_called_once()
        assert result == expected

    def test_pyaes_not_imported_when_cryptg_available(self):
        """_get_software_aes is not called when cryptg is present."""
        import telethon.crypto.aes as aes_mod

        mock_cryptg = unittest.mock.MagicMock()
        mock_cryptg.encrypt_ige.return_value = b'\x00' * 16

        with unittest.mock.patch.object(aes_mod, 'cryptg', mock_cryptg), \
             unittest.mock.patch.object(aes_mod, '_get_software_aes') as mock_sw:
            key = os.urandom(32)
            iv = os.urandom(32)
            aes_mod.AES.encrypt_ige(b'\x00' * 16, key, iv)

        mock_sw.assert_not_called()
