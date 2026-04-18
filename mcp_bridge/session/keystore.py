"""
Keystore: load or create the session root key and salt.

Two key sources are supported (in priority order):
1. OS keyring (service 'telethon-mcp-bridge', accounts '{session_name}:key'
   and '{session_name}:salt').
2. Environment variables TELETHON_SESSION_KEY and TELETHON_SESSION_SALT
   (both base64-encoded 32 bytes).

Passphrase source is intentionally omitted — keyring + env-var cover both
interactive and headless use cases with a smaller implementation surface.

Error classes KeyringUnavailableError and ConfigInvalidError are defined here
and will be moved to mcp_bridge/errors.py in Sprint 3.
"""
import base64
import logging
import os

import keyring

__log__ = logging.getLogger(__name__)

KEYRING_SERVICE = "telethon-mcp-bridge"


class KeyringUnavailableError(Exception):
    """Raised when the OS keyring is unavailable and no env-var fallback exists."""


class ConfigInvalidError(Exception):
    """Raised when key material is present from multiple sources but they differ."""


def _load_from_keyring(session_name: str) -> tuple[bytes | None, bytes | None]:
    """Return (key_bytes, salt_bytes) from the OS keyring, or (None, None) if absent."""
    try:
        key_b64 = keyring.get_password(KEYRING_SERVICE, f"{session_name}:key")
        salt_b64 = keyring.get_password(KEYRING_SERVICE, f"{session_name}:salt")
    except Exception as exc:
        __log__.debug("Keyring lookup failed: %s", exc)
        raise KeyringUnavailableError(
            f"OS keyring lookup failed for session '{session_name}': {exc}"
        ) from exc

    if key_b64 is None and salt_b64 is None:
        return None, None

    # Partial presence is an inconsistent state — operator must fix manually.
    if key_b64 is None or salt_b64 is None:
        missing = "key" if key_b64 is None else "salt"
        raise ConfigInvalidError(
            f"Keyring has only one of key/salt for session '{session_name}'; "
            f"'{missing}' is missing. This is an inconsistent state — "
            f"fix by storing both or clearing both from the keyring."
        )

    return base64.b64decode(key_b64), base64.b64decode(salt_b64)


def _load_from_env() -> tuple[bytes | None, bytes | None]:
    """Return (key_bytes, salt_bytes) from environment variables, or (None, None)."""
    key_b64 = os.environ.get("TELETHON_SESSION_KEY")
    salt_b64 = os.environ.get("TELETHON_SESSION_SALT")

    if key_b64 is None and salt_b64 is None:
        return None, None

    if key_b64 is None or salt_b64 is None:
        missing = "TELETHON_SESSION_KEY" if key_b64 is None else "TELETHON_SESSION_SALT"
        raise ConfigInvalidError(
            f"Only one of TELETHON_SESSION_KEY / TELETHON_SESSION_SALT is set; "
            f"'{missing}' is missing. Both must be provided together."
        )

    return base64.b64decode(key_b64), base64.b64decode(salt_b64)


def _store_in_keyring(session_name: str, key: bytes, salt: bytes) -> None:
    """Persist key and salt in the OS keyring."""
    keyring.set_password(KEYRING_SERVICE, f"{session_name}:key", base64.b64encode(key).decode())
    keyring.set_password(KEYRING_SERVICE, f"{session_name}:salt", base64.b64encode(salt).decode())


def load_or_create(session_name: str) -> tuple[bytes, bytes]:
    """Load the root key and salt, generating them on first run.

    Priority:
    1. OS keyring — if both key and salt are present, return them.
    2. Env vars — if TELETHON_SESSION_KEY + TELETHON_SESSION_SALT are set and
       keyring is empty, return them.
    3. First-run — if neither source has material, generate random key+salt,
       store in keyring, return them.

    Mixed-source mismatch (both keyring and env vars have material but differ)
    raises ConfigInvalidError.

    Raises KeyringUnavailableError if keyring fails AND env vars are absent.

    Returns:
        (root_key_32, salt_32) — both are 32-byte secrets.
    """
    keyring_err = None
    keyring_key: bytes | None = None
    keyring_salt: bytes | None = None

    try:
        keyring_key, keyring_salt = _load_from_keyring(session_name)
    except KeyringUnavailableError as exc:
        keyring_err = exc
    # ConfigInvalidError from keyring (partial presence) propagates immediately.

    env_key, env_salt = _load_from_env()
    # ConfigInvalidError from env (partial presence) propagates immediately.

    keyring_has_material = keyring_key is not None
    env_has_material = env_key is not None

    if keyring_has_material and env_has_material:
        # Both sources have material — check for mismatch.
        if keyring_key != env_key or keyring_salt != env_salt:
            raise ConfigInvalidError(
                "Key/salt material from the OS keyring and from "
                "TELETHON_SESSION_KEY/TELETHON_SESSION_SALT differ. "
                "This likely means the session was re-keyed outside this process. "
                "Resolve by clearing one of the sources and restarting."
            )
        __log__.debug("Key material from keyring and env vars match — using keyring copy.")
        return keyring_key, keyring_salt

    if keyring_has_material:
        __log__.debug("Loaded key/salt from OS keyring for session '%s'.", session_name)
        return keyring_key, keyring_salt

    if env_has_material:
        __log__.debug("Loaded key/salt from env vars for session '%s'.", session_name)
        return env_key, env_salt

    # Neither source has material — first-run or keyring unavailable.
    if keyring_err is not None:
        raise KeyringUnavailableError(
            f"No key material available for session '{session_name}': "
            f"OS keyring failed ({keyring_err}) and "
            f"TELETHON_SESSION_KEY / TELETHON_SESSION_SALT are not set."
        ) from keyring_err

    # True first-run: generate and persist.
    key = os.urandom(32)
    salt = os.urandom(32)
    try:
        _store_in_keyring(session_name, key, salt)
    except Exception as exc:
        # M-2: if the keyring write fails, do not return an unpersisted key.
        # The caller must fix the keyring or supply env vars instead.
        raise KeyringUnavailableError(
            f"keyring write failed; set TELETHON_SESSION_KEY + TELETHON_SESSION_SALT "
            f"env vars instead: {exc}"
        ) from exc
    __log__.info(
        "First-run: generated new key+salt for session '%s' and stored in keyring.",
        session_name,
    )
    return key, salt
