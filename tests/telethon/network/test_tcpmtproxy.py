"""
Tests for MTProxyIO.init_header — specifically the random[4:8] slice fix (M-3).

Before the fix, `random[4:4]` always returned b'' so the all-zeros guard on
bytes 4-8 was never triggered.  After the fix, a random whose bytes 4-8 are
all zeros must be rejected and a second urandom call must be made.
"""
import os
from unittest.mock import patch, call

from telethon.network.connection.tcpmtproxy import MTProxyIO
from telethon.network.connection.tcpintermediate import IntermediatePacketCodec


SECRET = bytes(range(16))  # 16-byte secret (arbitrary)
DC_ID = 1


def _make_random(*, zero_bytes_4_to_8: bool) -> bytes:
    """Return a 64-byte value that is otherwise valid but has bytes 4-8
    either all-zero (bad) or non-zero (good)."""
    data = bytearray(64)
    # Ensure byte 0 != 0xef
    data[0] = 0x01
    # Ensure bytes 0-3 are not a blocked keyword
    data[0:4] = b'\x01\x02\x03\x04'
    if zero_bytes_4_to_8:
        data[4:8] = b'\x00\x00\x00\x00'
    else:
        data[4:8] = b'\x01\x02\x03\x04'
    return bytes(data)


def test_bad_random_is_rejected_and_retried():
    """
    When os.urandom first returns a 64-byte value whose bytes 4-8 are all
    zeros, init_header must discard it and call os.urandom a second time.
    """
    bad_random = _make_random(zero_bytes_4_to_8=True)
    good_random = _make_random(zero_bytes_4_to_8=False)

    with patch('os.urandom', side_effect=[bad_random, good_random]) as mock_urandom:
        MTProxyIO.init_header(SECRET, DC_ID, IntermediatePacketCodec)

    assert mock_urandom.call_count >= 2, (
        f"Expected at least 2 calls to os.urandom (bad random rejected), "
        f"got {mock_urandom.call_count}"
    )
    # Both calls should have been for 64 bytes
    for c in mock_urandom.call_args_list:
        assert c == call(64)


def test_good_random_is_accepted_immediately():
    """
    When the first os.urandom result is already valid, init_header must not
    call urandom a second time (no unnecessary retries).
    """
    good_random = _make_random(zero_bytes_4_to_8=False)

    with patch('os.urandom', side_effect=[good_random]) as mock_urandom:
        MTProxyIO.init_header(SECRET, DC_ID, IntermediatePacketCodec)

    assert mock_urandom.call_count == 1, (
        f"Expected exactly 1 call to os.urandom, got {mock_urandom.call_count}"
    )


def test_init_header_returns_tuple_of_three():
    """Smoke test: init_header always returns (header, encryptor, decryptor)."""
    good_random = _make_random(zero_bytes_4_to_8=False)

    with patch('os.urandom', return_value=good_random):
        result = MTProxyIO.init_header(SECRET, DC_ID, IntermediatePacketCodec)

    assert isinstance(result, tuple) and len(result) == 3
    header, encryptor, decryptor = result
    assert len(header) == 64
