import struct

from .connection import Connection, PacketCodec

MAX_PACKET_SIZE = 2 * 1024 * 1024


class AbridgedPacketCodec(PacketCodec):
    tag = b'\xef'
    obfuscate_tag = b'\xef\xef\xef\xef'

    def encode_packet(self, data):
        length = len(data) >> 2
        if length < 127:
            length = struct.pack('B', length)
        else:
            length = b'\x7f' + int.to_bytes(length, 3, 'little')
        return length + data

    async def read_packet(self, reader):
        length = struct.unpack('<B', await reader.readexactly(1))[0]
        if length >= 127:
            length = struct.unpack(
                '<i', await reader.readexactly(3) + b'\0')[0]

        packet_size = length << 2
        if packet_size <= 0 or packet_size > MAX_PACKET_SIZE:
            raise RuntimeError(
                'Abridged packet size {} is invalid or exceeds maximum'.format(packet_size))
        return await reader.readexactly(packet_size)


class ConnectionTcpAbridged(Connection):
    """
    This is the mode with the lowest overhead, as it will
    only require 1 byte if the packet length is less than
    508 bytes (127 << 2, which is very common).
    """
    packet_codec = AbridgedPacketCodec
