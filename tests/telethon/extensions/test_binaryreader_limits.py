import struct
import pytest
from telethon.extensions.binaryreader import BinaryReader, MAX_VECTOR_SIZE, MAX_RECURSION_DEPTH


def test_vector_rejects_excessive_count():
    """M-5: Vectors with more than MAX_VECTOR_SIZE elements must be rejected."""
    data = struct.pack('<I', 0x1cb5c415) + struct.pack('<i', MAX_VECTOR_SIZE + 1)
    reader = BinaryReader(data)
    with pytest.raises(RuntimeError, match='Vector size'):
        reader.tgread_object()


def test_tgread_vector_rejects_excessive_count():
    """M-5: tgread_vector with huge count must be rejected."""
    data = struct.pack('<I', 0x1cb5c415) + struct.pack('<i', MAX_VECTOR_SIZE + 1)
    reader = BinaryReader(data)
    with pytest.raises(RuntimeError, match='Vector size'):
        reader.tgread_vector()


def test_vector_accepts_valid_count():
    """Small vectors should parse normally."""
    # Vector of 2 boolTrue values
    data = struct.pack('<I', 0x1cb5c415)  # vector constructor
    data += struct.pack('<i', 2)           # count=2
    data += struct.pack('<I', 0x997275b5)  # boolTrue
    data += struct.pack('<I', 0x997275b5)  # boolTrue
    reader = BinaryReader(data)
    result = reader.tgread_object()
    assert result == [True, True]


def test_recursion_depth_limit():
    """M-6: Deeply nested objects must be rejected."""
    reader = BinaryReader(b'\x00' * 1000)
    reader._depth = MAX_RECURSION_DEPTH  # Simulate max depth
    with pytest.raises(RuntimeError, match='depth'):
        reader.tgread_object()
