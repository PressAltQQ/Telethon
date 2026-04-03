import gzip
import struct
import pytest
from telethon.tl.core.gzippacked import GzipPacked
from telethon.extensions import BinaryReader
from telethon.tl.tlobject import TLObject


def test_gzip_normal_data_works():
    """Normal-sized data should decompress fine."""
    data = b'hello world' * 100
    compressed = gzip.compress(data)
    constructor = struct.pack('<I', GzipPacked.CONSTRUCTOR_ID)
    tl_bytes = TLObject.serialize_bytes(compressed)

    with BinaryReader(constructor + tl_bytes) as reader:
        result = GzipPacked.read(reader)
    assert result == data


def test_gzip_rejects_decompression_bomb():
    """Data exceeding MAX_DECOMPRESSED_SIZE should be rejected."""
    bomb_size = GzipPacked.MAX_DECOMPRESSED_SIZE + 1024 * 1024
    bomb_data = gzip.compress(b'\x00' * bomb_size)
    constructor = struct.pack('<I', GzipPacked.CONSTRUCTOR_ID)
    tl_bytes = TLObject.serialize_bytes(bomb_data)

    with BinaryReader(constructor + tl_bytes) as reader:
        with pytest.raises(ValueError, match='exceeds maximum'):
            GzipPacked.read(reader)
