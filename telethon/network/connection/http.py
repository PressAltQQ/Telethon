import asyncio

from .connection import Connection, PacketCodec

MAX_PACKET_SIZE = 2 * 1024 * 1024

SSL_PORT = 443


class HttpPacketCodec(PacketCodec):
    tag = None
    obfuscate_tag = None

    def encode_packet(self, data):
        # Sanitize to prevent CRLF header injection
        ip = str(self._conn._ip).replace('\r', '').replace('\n', '')
        port = int(self._conn._port)
        return ('POST /api HTTP/1.1\r\n'
                'Host: {}:{}\r\n'
                'Content-Type: application/x-www-form-urlencoded\r\n'
                'Connection: keep-alive\r\n'
                'Keep-Alive: timeout=100000, max=10000000\r\n'
                'Content-Length: {}\r\n\r\n'
                .format(ip, port, len(data))
                .encode('ascii') + data)

    async def read_packet(self, reader):
        while True:
            line = await reader.readline()
            if not line or line[-1] != b'\n':
                raise asyncio.IncompleteReadError(line, None)

            if line.lower().startswith(b'content-length: '):
                await reader.readexactly(2)
                length = int(line[16:-2])
                if length <= 0 or length > MAX_PACKET_SIZE:
                    raise RuntimeError(
                        'HTTP content length {} is invalid or exceeds maximum'.format(length))
                return await reader.readexactly(length)


class ConnectionHttp(Connection):
    packet_codec = HttpPacketCodec

    async def connect(self, timeout=None, ssl=None):
        await super().connect(timeout=timeout, ssl=self._port == SSL_PORT)
