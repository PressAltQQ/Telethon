try:
    from isal import igzip as gzip
except ImportError:
    import gzip
import struct

from .. import TLObject


class GzipPacked(TLObject):
    CONSTRUCTOR_ID = 0x3072cfa1
    MAX_DECOMPRESSED_SIZE = 16 * 1024 * 1024  # 16 MB

    def __init__(self, data):
        self.data = data

    @staticmethod
    def gzip_if_smaller(content_related, data):
        """Calls bytes(request), and based on a certain threshold,
           optionally gzips the resulting data. If the gzipped data is
           smaller than the original byte array, this is returned instead.

           Note that this only applies to content related requests.
        """
        if content_related and len(data) > 512:
            gzipped = bytes(GzipPacked(data))
            return gzipped if len(gzipped) < len(data) else data
        else:
            return data

    def __bytes__(self):
        return struct.pack('<I', GzipPacked.CONSTRUCTOR_ID) + \
               TLObject.serialize_bytes(gzip.compress(self.data))

    @staticmethod
    def read(reader):
        constructor = reader.read_int(signed=False)
        assert constructor == GzipPacked.CONSTRUCTOR_ID
        decompressed = gzip.decompress(reader.tgread_bytes())
        if len(decompressed) > GzipPacked.MAX_DECOMPRESSED_SIZE:
            raise ValueError(
                'Decompressed data exceeds maximum allowed size '
                '({} > {})'.format(len(decompressed), GzipPacked.MAX_DECOMPRESSED_SIZE)
            )
        return decompressed

    @classmethod
    def from_reader(cls, reader):
        decompressed = gzip.decompress(reader.tgread_bytes())
        if len(decompressed) > cls.MAX_DECOMPRESSED_SIZE:
            raise ValueError(
                'Decompressed data exceeds maximum allowed size '
                '({} > {})'.format(len(decompressed), cls.MAX_DECOMPRESSED_SIZE)
            )
        return GzipPacked(decompressed)

    def to_dict(self):
        return {
            '_': 'GzipPacked',
            'data': self.data
        }
