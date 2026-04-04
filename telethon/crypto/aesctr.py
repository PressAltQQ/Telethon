"""
This module holds the AESModeCTR wrapper class.
"""
import pyaes


class AESModeCTR:
    """Wrapper around pyaes.AESModeOfOperationCTR mode with custom IV"""
    # TODO Maybe make a pull request to pyaes to support iv on CTR

    def __init__(self, key, iv):
        """
        Initializes the AES CTR mode with the given key/iv pair.

        :param key: the key to be used as bytes.
        :param iv: the bytes initialization vector. Must have a length of 16.
        """
        # TODO Use libssl if available
        assert isinstance(key, bytes)
        assert isinstance(iv, bytes)
        assert len(iv) == 16
        counter = pyaes.Counter(initial_value=int.from_bytes(iv, 'big'))
        self._aes = pyaes.AESModeOfOperationCTR(key, counter=counter)

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
