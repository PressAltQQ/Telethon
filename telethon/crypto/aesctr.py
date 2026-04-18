"""
This module holds the AESModeCTR wrapper class.
"""
import os
import logging

__log__ = logging.getLogger(__name__)

# Track whether we have already warned about pyaes fallback in this process.
_pyaes_warning_emitted = False


def _get_software_aes():
    """Return the pyaes module for software AES fallback.

    Only permitted when TELETHON_ALLOW_PYAES=1 (test environments).
    Emits a WARNING on the first call to signal unintended runtime use.
    Raises ImportError if the env var is not set (production guard).
    """
    global _pyaes_warning_emitted
    if os.environ.get("TELETHON_ALLOW_PYAES") == "1":
        if not _pyaes_warning_emitted:
            __log__.warning(
                "pyaes fallback active — intended for tests only"
            )
            _pyaes_warning_emitted = True
        import pyaes  # noqa: PLC0415
        return pyaes
    raise ImportError(
        "cryptg is required for runtime use; "
        "set TELETHON_ALLOW_PYAES=1 only in test environments"
    )


class AESModeCTR:
    """Wrapper around pyaes.AESModeOfOperationCTR mode with custom IV"""
    # TODO Maybe make a pull request to pyaes to support iv on CTR

    def __init__(self, key, iv):
        """
        Initializes the AES CTR mode with the given key/iv pair.

        :param key: the key to be used as bytes.
        :param iv: the bytes initialization vector. Must have a length of 16.
        """
        pyaes = _get_software_aes()
        assert isinstance(key, bytes)
        self._aes = pyaes.AESModeOfOperationCTR(key)

        assert isinstance(iv, bytes)
        assert len(iv) == 16
        self._aes._counter._counter = list(iv)

    def encrypt(self, data):
        """
        Encrypts the given plain text through AES CTR.

        :param data: the plain text to be encrypted.
        :return: the encrypted cipher text.
        """
        return self._aes.encrypt(data)

    def decrypt(self, data):
        """
        Decrypts the given cipher text through AES CTR

        :param data: the cipher text to be decrypted.
        :return: the decrypted plain text.
        """
        return self._aes.decrypt(data)
