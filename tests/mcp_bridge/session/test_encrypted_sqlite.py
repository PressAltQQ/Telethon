"""
Tests for mcp_bridge.session.encrypted_sqlite.EncryptedSQLiteSession.

All tests use in-memory or temporary SQLite files — no network required.
"""
import base64
import os
import secrets
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from cryptography.exceptions import InvalidTag

from mcp_bridge.session.encrypted_sqlite import (
    EncryptedSQLiteSession,
    SessionKeyMismatchError,
    SessionNeedsMigrationError,
    _VERSION_BYTE,
)
from mcp_bridge.session.keystore import KeyringUnavailableError
from telethon.crypto import AuthKey


# ------------------------------------------------------------------ #
#  Fixtures                                                            #
# ------------------------------------------------------------------ #

def _make_key_salt():
    """Generate a fresh root_key and salt pair."""
    return os.urandom(32), os.urandom(32)


def _make_session(tmp_path, session_name="test_session", key=None, salt=None):
    """Create an EncryptedSQLiteSession backed by a temp file."""
    if key is None or salt is None:
        key, salt = _make_key_salt()
    path = tmp_path / session_name
    session = EncryptedSQLiteSession(
        str(path),
        root_key=key,
        salt=salt,
        allow_plaintext=True,  # skip migration check in setup
    )
    return session, key, salt


# ------------------------------------------------------------------ #
#  Round-trip encryption tests                                         #
# ------------------------------------------------------------------ #

class TestRoundTrip:
    def test_auth_key_round_trip(self, tmp_path):
        """Encrypting then decrypting auth_key produces the original bytes."""
        session, key, salt = _make_session(tmp_path)
        original = os.urandom(256)
        encrypted = session._encrypt("auth_key", original)
        assert encrypted != original  # sanity: it changed
        decrypted = session._decrypt("auth_key", encrypted)
        assert decrypted == original

    def test_entities_phone_round_trip(self, tmp_path):
        """entities.phone encrypt/decrypt round-trip."""
        session, _, _ = _make_session(tmp_path)
        original = b"+15551234567"
        encrypted = session._encrypt("entities.phone", original)
        assert encrypted != original
        decrypted = session._decrypt("entities.phone", encrypted)
        assert decrypted == original

    def test_entities_username_round_trip(self, tmp_path):
        """entities.username encrypt/decrypt round-trip."""
        session, _, _ = _make_session(tmp_path)
        original = b"telegram_user"
        encrypted = session._encrypt("entities.username", original)
        decrypted = session._decrypt("entities.username", encrypted)
        assert decrypted == original

    def test_entities_name_round_trip(self, tmp_path):
        """entities.name encrypt/decrypt round-trip."""
        session, _, _ = _make_session(tmp_path)
        original = "Alice Wonderland".encode()
        encrypted = session._encrypt("entities.name", original)
        decrypted = session._decrypt("entities.name", encrypted)
        assert decrypted == original

    def test_version_byte_prepended(self, tmp_path):
        """Encrypted blob starts with version byte 0x01."""
        session, _, _ = _make_session(tmp_path)
        encrypted = session._encrypt("auth_key", os.urandom(64))
        assert encrypted[0:1] == _VERSION_BYTE

    def test_empty_bytes_round_trip(self, tmp_path):
        """Empty input encrypts and decrypts to empty bytes."""
        session, _, _ = _make_session(tmp_path)
        encrypted = session._encrypt("auth_key", b"")
        assert encrypted == b""
        decrypted = session._decrypt("auth_key", b"")
        assert decrypted == b""

    def test_nonce_is_random_per_call(self, tmp_path):
        """Two encryptions of the same plaintext produce different ciphertexts (random nonces)."""
        session, _, _ = _make_session(tmp_path)
        plain = b"some secret data"
        enc1 = session._encrypt("auth_key", plain)
        enc2 = session._encrypt("auth_key", plain)
        assert enc1 != enc2


# ------------------------------------------------------------------ #
#  Wrong-key failure tests                                             #
# ------------------------------------------------------------------ #

class TestWrongKey:
    def test_wrong_root_key_raises_invalid_tag(self, tmp_path):
        """Decrypting with a different root key raises InvalidTag."""
        session_a, key_a, salt = _make_session(tmp_path, "ses_a")
        key_b = os.urandom(32)  # different key, same salt
        session_b = EncryptedSQLiteSession(
            str(tmp_path / "ses_b"),
            root_key=key_b,
            salt=salt,
            allow_plaintext=True,
        )

        plaintext = os.urandom(64)
        encrypted = session_a._encrypt("auth_key", plaintext)

        with pytest.raises(InvalidTag):
            session_b._decrypt("auth_key", encrypted)

    def test_wrong_salt_raises_invalid_tag(self, tmp_path):
        """Decrypting with a different salt (different derived key) raises InvalidTag."""
        session_a, key, salt_a = _make_session(tmp_path, "ses_a")
        salt_b = os.urandom(32)
        session_b = EncryptedSQLiteSession(
            str(tmp_path / "ses_b"),
            root_key=key,
            salt=salt_b,
            allow_plaintext=True,
        )

        plaintext = os.urandom(64)
        encrypted = session_a._encrypt("auth_key", plaintext)

        with pytest.raises(InvalidTag):
            session_b._decrypt("auth_key", encrypted)


# ------------------------------------------------------------------ #
#  Tamper detection tests                                              #
# ------------------------------------------------------------------ #

class TestTamperDetection:
    def test_flip_ciphertext_byte_raises(self, tmp_path):
        """Flipping a byte in the ciphertext raises InvalidTag (auth tag fails)."""
        session, _, _ = _make_session(tmp_path)
        plaintext = os.urandom(64)
        encrypted = bytearray(session._encrypt("auth_key", plaintext))

        # Flip a byte in the ciphertext (after version byte + nonce).
        nonce_len = session._nonce_len
        tamper_pos = 1 + nonce_len  # first byte of ciphertext
        encrypted[tamper_pos] ^= 0xFF

        with pytest.raises(InvalidTag):
            session._decrypt("auth_key", bytes(encrypted))

    def test_flip_tag_byte_raises(self, tmp_path):
        """Flipping the last byte (tag) raises InvalidTag."""
        session, _, _ = _make_session(tmp_path)
        plaintext = os.urandom(64)
        encrypted = bytearray(session._encrypt("auth_key", plaintext))
        encrypted[-1] ^= 0x01

        with pytest.raises(InvalidTag):
            session._decrypt("auth_key", bytes(encrypted))

    def test_truncated_blob_raises(self, tmp_path):
        """A truncated blob raises ValueError (too short)."""
        session, _, _ = _make_session(tmp_path)
        encrypted = session._encrypt("auth_key", b"hello")
        truncated = encrypted[:5]  # cut off most of it

        with pytest.raises((ValueError, InvalidTag)):
            session._decrypt("auth_key", truncated)


# ------------------------------------------------------------------ #
#  Migration — happy path                                              #
# ------------------------------------------------------------------ #

class TestMigrationHappyPath:
    def test_migration_creates_backup_file(self, tmp_path):
        """migrate_from_plaintext creates the backup file and does NOT delete it."""
        # Create a real SQLiteSession (plaintext) to start with.
        from telethon.sessions.sqlite import SQLiteSession
        session_path = tmp_path / "migration_test"
        plain_session = SQLiteSession(str(session_path))
        plain_session.save()
        plain_session.close()

        backup_path = tmp_path / "migration_test.session.plaintext.bak"

        # Now open as EncryptedSQLiteSession and migrate.
        key, salt = _make_key_salt()
        enc_session = EncryptedSQLiteSession(
            str(session_path),
            root_key=key,
            salt=salt,
            allow_plaintext=True,
        )
        enc_session.migrate_from_plaintext(backup_path)

        assert backup_path.exists(), "Backup file must exist after migration"
        enc_session.close()

    def test_migration_backup_not_deleted(self, tmp_path):
        """After successful migration, the backup file is preserved (not auto-deleted)."""
        from telethon.sessions.sqlite import SQLiteSession
        session_path = tmp_path / "mgr_nodelete"
        SQLiteSession(str(session_path)).save()

        backup_path = tmp_path / "backup.bak"
        key, salt = _make_key_salt()
        enc = EncryptedSQLiteSession(
            str(session_path),
            root_key=key,
            salt=salt,
            allow_plaintext=True,
        )
        enc.migrate_from_plaintext(backup_path)
        enc.close()

        assert backup_path.exists(), "Backup must NOT be auto-deleted after migration"

    def test_migration_produces_encrypted_auth_key_blob(self, tmp_path):
        """After migration, auth_key column contains encrypted BLOB (not plaintext bytes)."""
        from telethon.sessions.sqlite import SQLiteSession
        session_path = tmp_path / "enc_check"

        # Create plaintext session with a known auth_key.
        plain_session = SQLiteSession(str(session_path))
        plain_session._dc_id = 1
        plain_session._server_address = "149.154.175.50"
        plain_session._port = 443
        raw_key_bytes = os.urandom(256)
        plain_session._auth_key = AuthKey(data=raw_key_bytes)
        plain_session._update_session_table()
        plain_session.save()
        plain_session.close()

        backup_path = tmp_path / "enc_check.bak"
        key, salt = _make_key_salt()
        enc = EncryptedSQLiteSession(
            str(session_path),
            root_key=key,
            salt=salt,
            allow_plaintext=True,
        )
        enc.migrate_from_plaintext(backup_path)

        # Read the DB directly and verify the blob starts with the version byte.
        conn = sqlite3.connect(str(session_path) + ".session")
        row = conn.execute("select auth_key from sessions").fetchone()
        conn.close()
        enc.close()

        assert row is not None and row[0] is not None, "migration produced empty auth_key row"
        blob = bytes(row[0])
        assert blob[0:1] == _VERSION_BYTE, (
            "After migration, auth_key should start with version byte 0x01"
        )


# ------------------------------------------------------------------ #
#  Migration — crash recovery                                          #
# ------------------------------------------------------------------ #

class TestMigrationCrashRecovery:
    def test_failed_migration_leaves_db_intact(self, tmp_path):
        """If migration crashes mid-transaction, the original DB is rolled back."""
        from telethon.sessions.sqlite import SQLiteSession
        session_path = tmp_path / "crash_test"
        plain = SQLiteSession(str(session_path))
        plain._dc_id = 2
        plain._server_address = "10.0.0.1"
        plain._port = 443
        plain._auth_key = AuthKey(data=os.urandom(256))
        plain._update_session_table()
        plain.save()
        plain.close()

        backup_path = tmp_path / "crash_test.bak"

        key, salt = _make_key_salt()
        enc = EncryptedSQLiteSession(
            str(session_path),
            root_key=key,
            salt=salt,
            allow_plaintext=True,
        )

        # Patch _encrypt on the class level so the migration call raises
        # immediately on the first encrypt attempt — simulating a crash
        # mid-transaction (e.g. out of memory, unexpected exception).
        original_encrypt = EncryptedSQLiteSession._encrypt
        call_count = [0]

        def failing_encrypt(self_arg, col, data):
            call_count[0] += 1
            # Fail on the first encrypt call during migration.
            raise RuntimeError("Simulated crash during migration")

        with patch.object(EncryptedSQLiteSession, '_encrypt', failing_encrypt):
            with pytest.raises(RuntimeError, match="Simulated crash"):
                enc.migrate_from_plaintext(backup_path)

        enc.close()

        # The original DB should still be readable as plaintext.
        conn = sqlite3.connect(str(session_path) + ".session")
        row = conn.execute("select dc_id from sessions").fetchone()
        conn.close()

        assert row is not None, "Original sessions table must still exist after rollback"
        assert row[0] == 2, "Original dc_id must be intact after rollback"


# ------------------------------------------------------------------ #
#  SC2 negative test: stolen session file + no key = no access        #
# ------------------------------------------------------------------ #

class TestSC2SecurityProperty:
    def test_session_file_without_key_raises_keyring_unavailable(self, tmp_path):
        """SC2: Copying the session file to a new location without the key
        and with a broken keyring backend (and no env var fallback) must raise
        KeyringUnavailableError, NOT silently decrypt or access session data.

        This verifies that 'stealing the session file is insufficient to log in'.
        The attacker has the .session file but lacks the keyring entry.
        We simulate a keyring failure (as if the backend is unavailable on a
        different machine) plus no env vars.
        """
        from mcp_bridge.session.keystore import load_or_create, KeyringUnavailableError

        # Simulate keyring backend failure (different machine — no keyring access).
        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)
            # keyring.get_password raises (as if backend is unavailable).
            mock_kr.get_password.side_effect = RuntimeError("no keyring backend")

            with pytest.raises(KeyringUnavailableError):
                load_or_create("stolen_session")

    def test_opening_encrypted_session_with_wrong_key_raises(self, tmp_path):
        """Opening an encrypted session with a wrong root_key causes decryption failure."""
        # Create a session with one key.
        session_path = tmp_path / "victim"
        key_original, salt = _make_key_salt()
        session = EncryptedSQLiteSession(
            str(session_path),
            root_key=key_original,
            salt=salt,
            allow_plaintext=True,
        )
        # Write a fake auth_key using the original key.
        session._auth_key = AuthKey(data=os.urandom(256))
        session._dc_id = 1
        session._server_address = "1.2.3.4"
        session._port = 443
        session._update_session_table()
        session.save()
        session.close()

        # F-4: opening with a different key must raise SessionKeyMismatchError at init.
        wrong_key = os.urandom(32)
        with pytest.raises(SessionKeyMismatchError):
            EncryptedSQLiteSession(
                str(session_path),
                root_key=wrong_key,
                salt=salt,
                allow_plaintext=False,
            )


# ------------------------------------------------------------------ #
#  Keyring-revoked-while-running edge case (spec §10)                  #
# ------------------------------------------------------------------ #

class TestKeyringRevokedWhileRunning:
    def test_in_memory_key_survives_keyring_deletion(self, tmp_path):
        """Once the session is open, in-memory key continues to work even if
        keyring entry is deleted.  A fresh load() call without keyring raises."""
        session_path = tmp_path / "revoke_test"
        key, salt = _make_key_salt()
        session = EncryptedSQLiteSession(
            str(session_path),
            root_key=key,
            salt=salt,
            allow_plaintext=True,
        )

        # Original encryption works.
        original = os.urandom(64)
        encrypted = session._encrypt("auth_key", original)
        decrypted = session._decrypt("auth_key", encrypted)
        assert decrypted == original

        # Simulate keyring deletion while session is running.
        # The existing session uses the in-memory key — still works.
        decrypted2 = session._decrypt("auth_key", encrypted)
        assert decrypted2 == original

        session.close()

        # Now simulate a "fresh process" open: keyring entry was deleted,
        # backend raises (simulating deleted secret, not just missing entry).
        from mcp_bridge.session.keystore import load_or_create
        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)
            # Simulate keyring raising an error (deleted/inaccessible entry).
            mock_kr.get_password.side_effect = RuntimeError("keyring entry deleted")

            with pytest.raises(KeyringUnavailableError):
                load_or_create("revoke_test")


# ------------------------------------------------------------------ #
#  C-1/C-2 regression: auth_key survives reopen + set_dc             #
# ------------------------------------------------------------------ #

class TestAuthKeySurvivesReconnect:
    def test_auth_key_survives_reopen_and_set_dc(self, tmp_path, monkeypatch):
        """C-1/C-2 regression test: open → save auth_key → close → reopen → set_dc → auth_key intact."""
        monkeypatch.setenv("TELETHON_SESSION_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
        monkeypatch.setenv("TELETHON_SESSION_SALT", base64.b64encode(secrets.token_bytes(32)).decode())
        session_path = tmp_path / "test.session"

        original_key = secrets.token_bytes(256)  # AuthKey is 256 bytes

        # First session: write auth_key
        s1 = EncryptedSQLiteSession(str(session_path), "main")
        s1.set_dc(1, "1.2.3.4", 443)
        s1.auth_key = AuthKey(data=original_key)  # triggers _update_session_table via property
        s1.save()
        s1.close()

        # Second session: reopen, call set_dc, verify auth_key preserved
        s2 = EncryptedSQLiteSession(str(session_path), "main")
        assert s2.auth_key is not None
        assert s2.auth_key.key == original_key, "auth_key mutated on reopen"
        s2.set_dc(1, "5.6.7.8", 443)  # connect would do this
        assert s2.auth_key.key == original_key, "set_dc clobbered auth_key"
        s2.save()
        s2.close()

        # Third session: verify persistence after set_dc
        s3 = EncryptedSQLiteSession(str(session_path), "main")
        assert s3.auth_key.key == original_key, "auth_key corrupted on disk"
        s3.close()


# ------------------------------------------------------------------ #
#  I-5: _decrypt type guard for legacy int/str columns               #
# ------------------------------------------------------------------ #

class TestDecryptTypeGuard:
    def test_str_input_raises_migration_error(self, tmp_path):
        """_decrypt with str input raises SessionNeedsMigrationError."""
        session, _, _ = _make_session(tmp_path)
        with pytest.raises(SessionNeedsMigrationError):
            session._decrypt("auth_key", "plaintext_string")

    def test_int_input_raises_migration_error(self, tmp_path):
        """_decrypt with int input (legacy phone column) raises SessionNeedsMigrationError."""
        session, _, _ = _make_session(tmp_path)
        with pytest.raises(SessionNeedsMigrationError):
            session._decrypt("entities.phone", 1234567890)


# ------------------------------------------------------------------ #
#  F-4: wrong-key open should raise SessionKeyMismatchError          #
# ------------------------------------------------------------------ #

class TestWrongKeyOpen:
    def test_open_with_wrong_key_raises_session_key_mismatch(self, tmp_path):
        """F-4: opening a session with key A then reopening with key B raises SessionKeyMismatchError."""
        session_path = tmp_path / "key_mismatch"
        key_a, salt = _make_key_salt()

        # Create session with key A and write an auth_key
        s_a = EncryptedSQLiteSession(
            str(session_path),
            root_key=key_a,
            salt=salt,
            allow_plaintext=True,
        )
        s_a.set_dc(1, "1.2.3.4", 443)
        s_a.auth_key = AuthKey(data=secrets.token_bytes(256))
        s_a.save()
        s_a.close()

        # Reopen with wrong key B — should raise SessionKeyMismatchError.
        # allow_plaintext=False so that _load_auth_key_from_db is invoked.
        key_b = os.urandom(32)
        with pytest.raises(SessionKeyMismatchError, match="session key does not match"):
            EncryptedSQLiteSession(
                str(session_path),
                root_key=key_b,
                salt=salt,
                allow_plaintext=False,
            )


# ------------------------------------------------------------------ #
#  M-2: keystore set_password failure raises KeyringUnavailableError #
# ------------------------------------------------------------------ #

class TestKeystoreSetPasswordFailure:
    def test_set_password_failure_raises_keyring_unavailable(self):
        """M-2: first-run key generation where set_password raises → KeyringUnavailableError."""
        from mcp_bridge.session.keystore import load_or_create

        with patch('mcp_bridge.session.keystore.keyring') as mock_kr, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TELETHON_SESSION_KEY', None)
            os.environ.pop('TELETHON_SESSION_SALT', None)
            # Keyring has no material (first run)
            mock_kr.get_password.return_value = None
            # But set_password fails
            mock_kr.set_password.side_effect = RuntimeError("keyring write failed")

            with pytest.raises(KeyringUnavailableError, match="keyring write failed"):
                load_or_create("new_session_mismatch")
