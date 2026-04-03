"""
Tests for IntermediatePacketCodec max packet size enforcement (H-8).

The telethon package requires generated TL layer files that are not present in
this repository, so we isolate the import using module stubs to load only the
codec module under test.
"""
import asyncio
import importlib.util
import struct
import sys
import types

import pytest


def _load_codec_module():
    """Load tcpintermediate without triggering the full telethon import chain."""
    # Stub out the connection base module so the relative import resolves.
    stub_conn = types.ModuleType('telethon.network.connection.connection')

    class PacketCodec:
        def __init__(self, connection):
            self._connection = connection

    stub_conn.PacketCodec = PacketCodec
    stub_conn.Connection = object

    for name in ('telethon', 'telethon.network', 'telethon.network.connection'):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

    sys.modules['telethon.network.connection.connection'] = stub_conn

    import os
    repo_root = os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
        )
    )
    module_path = os.path.join(
        repo_root,
        'telethon', 'network', 'connection', 'tcpintermediate.py'
    )
    spec = importlib.util.spec_from_file_location(
        'telethon.network.connection.tcpintermediate', module_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_codec_module = _load_codec_module()
IntermediatePacketCodec = _codec_module.IntermediatePacketCodec


class FakeReader:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def readexactly(self, n):
        result = self._data[self._pos:self._pos + n]
        self._pos += n
        return result


def test_rejects_oversized_packet():
    huge_length = 100 * 1024 * 1024  # 100 MB
    header = struct.pack('<i', huge_length)
    reader = FakeReader(header + b'\x00' * 100)
    codec = IntermediatePacketCodec(None)

    with pytest.raises(ValueError, match='Invalid packet length'):
        asyncio.run(codec.read_packet(reader))


def test_rejects_negative_length():
    header = struct.pack('<i', -1)
    reader = FakeReader(header)
    codec = IntermediatePacketCodec(None)

    with pytest.raises(ValueError, match='Invalid packet length'):
        asyncio.run(codec.read_packet(reader))


def test_accepts_valid_packet():
    data = b'hello' * 100
    header = struct.pack('<i', len(data))
    reader = FakeReader(header + data)
    codec = IntermediatePacketCodec(None)

    result = asyncio.run(codec.read_packet(reader))
    assert result == data


def test_rejects_zero_length():
    header = struct.pack('<i', 0)
    reader = FakeReader(header)
    codec = IntermediatePacketCodec(None)

    with pytest.raises(ValueError, match='Invalid packet length'):
        asyncio.run(codec.read_packet(reader))


def test_accepts_max_boundary():
    """Packet exactly at MAX_PACKET_SIZE should be accepted."""
    max_size = IntermediatePacketCodec.MAX_PACKET_SIZE
    header = struct.pack('<i', max_size)
    data = b'\xab' * max_size
    reader = FakeReader(header + data)
    codec = IntermediatePacketCodec(None)

    result = asyncio.run(codec.read_packet(reader))
    assert len(result) == max_size


def test_rejects_one_over_max():
    """Packet one byte over MAX_PACKET_SIZE should be rejected."""
    over_max = IntermediatePacketCodec.MAX_PACKET_SIZE + 1
    header = struct.pack('<i', over_max)
    reader = FakeReader(header + b'\x00' * 100)
    codec = IntermediatePacketCodec(None)

    with pytest.raises(ValueError, match='Invalid packet length'):
        asyncio.run(codec.read_packet(reader))
