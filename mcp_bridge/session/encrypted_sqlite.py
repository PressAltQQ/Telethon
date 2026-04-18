"""
EncryptedSQLiteSession — a drop-in replacement for SQLiteSession that
transparently encrypts sensitive columns at rest.

Encrypted columns:
  sessions.auth_key
  entities.phone, entities.username, entities.name

Non-encrypted columns (needed for bootstrapping, not secret):
  dc_id, server_address, port, takeout_id, tmp_auth_key, entities.id,
  entities.hash, entities.date, and all sent_files / update_state columns.

Algorithm choice (made at __init__ time, logged once):
  Primary: ChaCha20Poly1305 with a 12-byte random nonce per encryption call.
  Note: cryptography's ChaCha20Poly1305 uses a 12-byte nonce (IETF variant),
  not XChaCha20's 24-byte nonce — the spec allows AESGCM as fallback but
  ChaCha20Poly1305 is the preferred choice here.

Key derivation:
  HKDF-SHA-256(ikm=root_key, salt=salt_32, info=b"telethon-mcp-bridge|v1|<column>",
               length=32) per column.

Storage layout per encrypted value:
  byte 0    : version byte 0x01 (reserves future algorithm migration)
  bytes 1…N : nonce (12 bytes for ChaCha20Poly1305, 12 bytes for AESGCM)
  bytes N+1…: ciphertext + authentication tag (16-byte GCM/Poly1305 tag appended)

Startup behaviour:
  If auth_key column is TEXT type (plaintext legacy session) and no explicit
  allow flag was given, raises SessionNeedsMigrationError.

Migration (migrate_from_plaintext):
  Atomic SQLite transaction: backup → v2 tables → copy+encrypt → verify → rename.
  Never auto-deletes the backup.
"""
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from telethon.crypto import AuthKey
from telethon.sessions.sqlite import SQLiteSession

__log__ = logging.getLogger(__name__)

# Version byte prepended to every encrypted blob (for future algorithm migration).
_VERSION_BYTE = b'\x01'

# HKDF domain-separation prefix (must stay identical forever — changing it
# breaks existing sessions by deriving wrong per-column keys).
_HKDF_PREFIX = b"telethon-mcp-bridge|v1|"

# Encrypted column names used in HKDF info strings.  Changing these strings
# breaks decryption of existing sessions.
_SESSION_ENCRYPTED_COLS = ["auth_key"]
_ENTITY_ENCRYPTED_COLS = ["phone", "username", "name"]


class SessionNeedsMigrationError(Exception):
    """Raised when a plaintext session is opened without migration flag."""


class SessionKeyMismatchError(Exception):
    """Raised when the session key does not match the stored encrypted data."""


class EncryptedSQLiteSession(SQLiteSession):
    """SQLiteSession subclass that encrypts auth_key and entity PII at rest.

    Usage (explicit key):
        key, salt = keystore.load_or_create(session_name)
        session = EncryptedSQLiteSession(session_name, root_key=key, salt=salt)

    Usage (keystore auto-load via env vars or OS keyring):
        session = EncryptedSQLiteSession(session_path, keystore_name)

    When called with two positional args (session_id, keystore_name), the class
    loads root_key and salt from keystore.load_or_create(keystore_name).
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        keystore_name: Optional[str] = None,
        *,
        root_key: Optional[bytes] = None,
        salt: Optional[bytes] = None,
        allow_plaintext: bool = False,
        store_tmp_auth_key_on_disk: bool = False,
    ):
        # Support two-positional-arg form: (session_path, keystore_name).
        # In this form, root_key and salt are loaded from the keystore.
        if keystore_name is not None and root_key is None and salt is None:
            from mcp_bridge.session import keystore as _keystore
            root_key, salt = _keystore.load_or_create(keystore_name)

        if root_key is None or salt is None:
            raise ValueError("root_key and salt must be provided (or supply a keystore_name)")
        if len(root_key) != 32:
            raise ValueError("root_key must be exactly 32 bytes")
        if len(salt) != 32:
            raise ValueError("salt must be exactly 32 bytes")

        self._root_key = root_key
        self._salt = salt

        # Derive per-column keys once at construction time.
        self._col_keys: dict[str, bytes] = {}
        for col in _SESSION_ENCRYPTED_COLS + [f"entities.{c}" for c in _ENTITY_ENCRYPTED_COLS]:
            self._col_keys[col] = self._derive_key(col)

        # Choose encryption algorithm.
        self._aead_cls = ChaCha20Poly1305
        self._nonce_len = 12
        __log__.info(
            "EncryptedSQLiteSession: using %s (nonce=%d bytes)",
            self._aead_cls.__name__,
            self._nonce_len,
        )

        # Let SQLiteSession initialise the DB (creates tables if needed).
        # We intercept reads/writes via overridden methods below.
        # We must NOT call super().__init__ yet if we need to check column
        # types first — so we do the plaintext check after init.
        super().__init__(
            session_id=session_id,
            store_tmp_auth_key_on_disk=store_tmp_auth_key_on_disk,
        )

        self._allow_plaintext = allow_plaintext

        if not allow_plaintext and session_id is not None:
            self._check_not_plaintext()

        # C-1/C-2 fix: after super().__init__ loads the DB, the parent has read
        # auth_key raw (ciphertext) into self._auth_key.  We must decrypt it now
        # so that self._auth_key holds the actual plaintext key.  If the blob is
        # empty (fresh session) we leave _auth_key as None.
        # Skip when allow_plaintext=True: the session may contain raw (unencrypted)
        # bytes and decryption would fail — callers using allow_plaintext must
        # call migrate_from_plaintext before relying on _auth_key.
        if not allow_plaintext:
            self._load_auth_key_from_db()

    # ------------------------------------------------------------------ #
    #  Internal crypto helpers                                             #
    # ------------------------------------------------------------------ #

    def _derive_key(self, column_name: str) -> bytes:
        """Derive a 32-byte column-specific key via HKDF-SHA-256."""
        info = _HKDF_PREFIX + column_name.encode()
        hkdf = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=self._salt,
            info=info,
        )
        return hkdf.derive(self._root_key)

    def _encrypt(self, column_name: str, plaintext: bytes) -> bytes:
        """Encrypt plaintext for a named column. Returns version+nonce+ciphertext."""
        if not plaintext:
            return b''
        key = self._col_keys[column_name]
        nonce = os.urandom(self._nonce_len)
        cipher = self._aead_cls(key)
        ciphertext = cipher.encrypt(nonce, plaintext, None)
        return _VERSION_BYTE + nonce + ciphertext

    def _decrypt(self, column_name: str, blob: bytes) -> bytes:
        """Decrypt a blob (version+nonce+ciphertext) for a named column."""
        if not blob:
            return b''
        if not isinstance(blob, (bytes, bytearray)):
            # I-5: legacy entity columns may store str or int (e.g. phone as integer).
            # Neither can be decrypted — signal migration is required.
            raise SessionNeedsMigrationError(
                f"Column '{column_name}' contains non-bytes data ({type(blob).__name__}). "
                f"Run migrate_from_plaintext() first."
            )
        if len(blob) < 1 + self._nonce_len + 1:
            raise ValueError(f"Blob for column '{column_name}' is too short to be valid")
        version = blob[0:1]
        if version != _VERSION_BYTE:
            raise ValueError(
                f"Unknown version byte 0x{version.hex()} in column '{column_name}'"
            )
        nonce = blob[1: 1 + self._nonce_len]
        ciphertext = blob[1 + self._nonce_len:]
        key = self._col_keys[column_name]
        cipher = self._aead_cls(key)
        # decrypt() raises cryptography.exceptions.InvalidTag on tamper/wrong-key.
        return cipher.decrypt(nonce, ciphertext, None)

    # ------------------------------------------------------------------ #
    #  Plaintext detection                                                 #
    # ------------------------------------------------------------------ #

    def _check_not_plaintext(self) -> None:
        """Detect legacy plaintext sessions and raise SessionNeedsMigrationError."""
        c = self._cursor()
        try:
            # Check the declared type of the auth_key column.
            # SQLiteSession stores auth_key as BLOB; plaintext TEXT would be unusual,
            # but we also check whether the actual data is non-bytes.
            rows = c.execute("select auth_key from sessions").fetchall()
            for row in rows:
                val = row[0]
                if val and not isinstance(val, bytes):
                    raise SessionNeedsMigrationError(
                        "auth_key column contains non-blob (plaintext?) data. "
                        "Run migrate_from_plaintext(backup_path) to encrypt the session."
                    )
        finally:
            c.close()

    def _load_auth_key_from_db(self) -> None:
        """C-1/C-2 fix: read and decrypt auth_key (and tmp_auth_key) from DB.

        Called once in __init__ after super().__init__ has loaded the raw
        (ciphertext) bytes into self._auth_key.  Replaces that value with the
        decrypted plaintext so downstream code always sees the real key.

        On a fresh session (no row or empty blob) this is a no-op.
        On wrong-key open, raises SessionKeyMismatchError (F-4).
        """
        from cryptography.exceptions import InvalidTag
        row = self._execute('select auth_key, tmp_auth_key from sessions')
        if row is None:
            self._auth_key = None
            self._tmp_auth_key = None
            return

        enc_key = row[0]
        if enc_key:
            try:
                decrypted = self._decrypt("auth_key", enc_key)
            except InvalidTag as exc:
                raise SessionKeyMismatchError(
                    "session key does not match stored data — "
                    "keyring entry may be stale or session encrypted with a different key"
                ) from exc
            self._auth_key = AuthKey(data=decrypted) if decrypted else None
        else:
            self._auth_key = None

        enc_tmp = row[1]
        if enc_tmp and self.store_tmp_auth_key_on_disk:
            self._tmp_auth_key = AuthKey(data=enc_tmp)
        else:
            self._tmp_auth_key = None

    # ------------------------------------------------------------------ #
    #  Overridden read/write methods                                       #
    # ------------------------------------------------------------------ #

    def _update_session_table(self) -> None:
        """Store session row with auth_key encrypted."""
        c = self._cursor()
        try:
            c.execute('delete from sessions')
            raw_key = self._auth_key.key if self._auth_key else b''
            raw_tmp = (
                self._tmp_auth_key.key
                if (self.store_tmp_auth_key_on_disk and self._tmp_auth_key)
                else b''
            )
            enc_key = self._encrypt("auth_key", raw_key)
            c.execute(
                'insert or replace into sessions values (?,?,?,?,?,?)',
                (
                    self._dc_id,
                    self._server_address,
                    self._port,
                    enc_key,
                    self._takeout_id,
                    raw_tmp,  # tmp_auth_key is intentionally not encrypted
                ),
            )
        finally:
            c.close()

    def set_dc(self, dc_id, server_address, port):
        """Override to update DC info while preserving the already-decrypted auth_key.

        C-1/C-2 fix: self._auth_key is already the decrypted plaintext (loaded in
        __init__ via _load_auth_key_from_db).  We update the in-memory DC fields
        and persist the row — encrypting self._auth_key.key in the process.
        We do NOT re-read the encrypted blob from DB, which would cause a
        double-decrypt race and corrupt the stored key.
        """
        from telethon.sessions.memory import MemorySession
        # Update in-memory DC fields only — does not touch auth_key.
        MemorySession.set_dc(self, dc_id, server_address, port)
        # Persist the row with the (correct, already-decrypted) self._auth_key re-encrypted.
        self._update_session_table()

    def process_entities(self, tlo) -> None:
        """Store entity PII columns (phone, username, name) encrypted."""
        if not self.save_entities:
            return

        rows = self._entities_to_rows(tlo)
        if not rows:
            return

        import time
        c = self._cursor()
        try:
            now = int(time.time())
            encrypted_rows = []
            for row in rows:
                # row = (id, hash, username, phone, name)
                entity_id, hash_, username, phone, name = row
                raw_phone = phone.encode() if isinstance(phone, str) else (phone or b'')
                raw_username = username.encode() if isinstance(username, str) else (username or b'')
                raw_name = name.encode() if isinstance(name, str) else (name or b'')
                enc_phone = self._encrypt("entities.phone", raw_phone)
                enc_username = self._encrypt("entities.username", raw_username)
                enc_name = self._encrypt("entities.name", raw_name)
                encrypted_rows.append((entity_id, hash_, enc_username, enc_phone, enc_name, now))
            c.executemany(
                'insert or replace into entities values (?,?,?,?,?,?)',
                encrypted_rows,
            )
        finally:
            c.close()

    def get_entity_rows_by_phone(self, phone):
        """Scan all entities and find the one whose decrypted phone matches."""
        c = self._cursor()
        try:
            rows = c.execute('select id, hash, phone from entities').fetchall()
        finally:
            c.close()

        phone_str = str(phone) if phone is not None else ''
        for entity_id, hash_, enc_phone in rows:
            if not enc_phone:
                continue
            try:
                dec_phone = self._decrypt("entities.phone", enc_phone)
                dec_str = dec_phone.decode() if dec_phone else ''
                if dec_str == phone_str:
                    return entity_id, hash_
            except Exception:
                continue
        return None

    def get_entity_rows_by_username(self, username):
        """Scan all entities and find the one whose decrypted username matches."""
        c = self._cursor()
        try:
            rows = c.execute('select id, hash, username, date from entities').fetchall()
        finally:
            c.close()

        username_lower = (username or '').lower()
        matches = []
        for entity_id, hash_, enc_username, date in rows:
            if not enc_username:
                continue
            try:
                dec_username = self._decrypt("entities.username", enc_username)
                dec_str = dec_username.decode() if dec_username else ''
                if dec_str.lower() == username_lower:
                    matches.append((entity_id, hash_, date))
            except Exception:
                continue

        if not matches:
            return None

        if len(matches) > 1:
            matches.sort(key=lambda t: t[2] or 0)

        return matches[-1][0], matches[-1][1]

    def get_entity_rows_by_name(self, name):
        """Scan all entities and find the one whose decrypted name matches."""
        c = self._cursor()
        try:
            rows = c.execute('select id, hash, name from entities').fetchall()
        finally:
            c.close()

        for entity_id, hash_, enc_name in rows:
            if not enc_name:
                continue
            try:
                dec_name = self._decrypt("entities.name", enc_name)
                dec_str = dec_name.decode() if dec_name else ''
                if dec_str == (name or ''):
                    return entity_id, hash_
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------ #
    #  Migration                                                           #
    # ------------------------------------------------------------------ #

    def migrate_from_plaintext(self, backup_path: Path) -> None:
        """Migrate a plaintext SQLite session to encrypted in-place.

        Steps (all-or-nothing SQLite transaction):
        1. Copy the DB file to backup_path with perms 0o600.
        2. Within ONE transaction:
           a. CREATE sessions_v2 (encrypted column types).
           b. Copy rows from sessions, encrypting auth_key as they go.
           c. Verify row count matches.
           d. Decrypt-round-trip one sample row.
           e. DROP sessions; RENAME sessions_v2 → sessions.
           f. Same for entities (phone, username, name).
        3. On success: print confirmation with backup path + manual-delete instruction.
        4. On failure: transaction auto-rolls-back; backup file is preserved; raise.
        5. NEVER auto-deletes backup.

        Raises:
            RuntimeError if the session is in-memory (no DB file).
            Any exception from the crypto or SQLite layer — transaction rolls back.
        """
        if self.filename == ':memory:':
            raise RuntimeError("Cannot migrate an in-memory session.")

        db_path = Path(self.filename)
        if not db_path.exists():
            raise FileNotFoundError(f"Session file not found: {db_path}")

        # Step 1: backup.
        shutil.copy2(str(db_path), str(backup_path))
        os.chmod(str(backup_path), 0o600)
        __log__.info("Migration backup written to %s", backup_path)

        conn = self._conn
        if conn is None:
            conn = sqlite3.connect(self.filename, check_same_thread=False)

        try:
            with conn:  # One transaction — auto-rollback on exception.
                # --- sessions table ---
                old_sessions = conn.execute("select * from sessions").fetchall()
                conn.execute("""
                    CREATE TABLE sessions_v2 (
                        dc_id integer primary key,
                        server_address text,
                        port integer,
                        auth_key blob,
                        takeout_id integer,
                        tmp_auth_key blob
                    )
                """)

                for row in old_sessions:
                    dc_id, server_address, port, auth_key_raw, takeout_id, tmp_auth_key = row
                    # auth_key_raw may be bytes (blob) or None.
                    raw = auth_key_raw if isinstance(auth_key_raw, bytes) else (auth_key_raw or b'')
                    enc_key = self._encrypt("auth_key", raw)
                    conn.execute(
                        "INSERT INTO sessions_v2 VALUES (?,?,?,?,?,?)",
                        (dc_id, server_address, port, enc_key, takeout_id, tmp_auth_key),
                    )

                # Verify row count.
                new_count = conn.execute("select count(*) from sessions_v2").fetchone()[0]
                if new_count != len(old_sessions):
                    raise RuntimeError(
                        f"sessions row count mismatch after migration: "
                        f"expected {len(old_sessions)}, got {new_count}"
                    )

                # Round-trip sample verification.
                if old_sessions:
                    sample = conn.execute("select auth_key from sessions_v2").fetchone()[0]
                    original_raw = (
                        old_sessions[0][3]
                        if isinstance(old_sessions[0][3], bytes)
                        else (old_sessions[0][3] or b'')
                    )
                    decrypted = self._decrypt("auth_key", sample)
                    if decrypted != original_raw:
                        raise RuntimeError("Round-trip verification failed for sessions.auth_key")

                conn.execute("DROP TABLE sessions")
                conn.execute("ALTER TABLE sessions_v2 RENAME TO sessions")

                # --- entities table ---
                old_entities = conn.execute("select * from entities").fetchall()
                conn.execute("""
                    CREATE TABLE entities_v2 (
                        id integer primary key,
                        hash integer not null,
                        username blob,
                        phone blob,
                        name blob,
                        date integer
                    )
                """)

                for row in old_entities:
                    entity_id, hash_, username, phone, name, date = row
                    raw_phone = (str(phone).encode() if phone is not None else b'')
                    raw_username = (username.encode() if isinstance(username, str) else (username or b''))
                    raw_name = (name.encode() if isinstance(name, str) else (name or b''))
                    enc_phone = self._encrypt("entities.phone", raw_phone)
                    enc_username = self._encrypt("entities.username", raw_username)
                    enc_name = self._encrypt("entities.name", raw_name)
                    conn.execute(
                        "INSERT INTO entities_v2 VALUES (?,?,?,?,?,?)",
                        (entity_id, hash_, enc_username, enc_phone, enc_name, date),
                    )

                new_ent_count = conn.execute("select count(*) from entities_v2").fetchone()[0]
                if new_ent_count != len(old_entities):
                    raise RuntimeError(
                        f"entities row count mismatch: "
                        f"expected {len(old_entities)}, got {new_ent_count}"
                    )

                conn.execute("DROP TABLE entities")
                conn.execute("ALTER TABLE entities_v2 RENAME TO entities")

        except Exception:
            # Transaction auto-rolled-back by the context manager.
            __log__.error(
                "Migration failed — database rolled back. Backup preserved at %s",
                backup_path,
            )
            raise

        __log__.info(
            "Migration successful. Backup of the plaintext session is at: %s — "
            "delete manually once you are satisfied the encrypted session works correctly: "
            "rm -- '%s'. DO NOT leave the plaintext backup on disk longer than necessary.",
            backup_path,
            backup_path,
        )
