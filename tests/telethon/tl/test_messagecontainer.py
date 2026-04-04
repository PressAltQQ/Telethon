import struct
import pytest
from telethon.tl.core.messagecontainer import MessageContainer
from telethon.extensions.binaryreader import BinaryReader


def _build_container_bytes(count):
    """Build raw bytes for a MessageContainer with `count` trivial messages."""
    buf = struct.pack('<i', count)
    for i in range(count):
        msg_id = struct.pack('<q', i + 1)      # 8 bytes
        seq_no = struct.pack('<i', i * 2)       # 4 bytes
        # Inner object: boolTrue (constructor 0x997275b5), length=4
        inner = struct.pack('<I', 0x997275b5)
        length = struct.pack('<i', len(inner))  # 4 bytes
        buf += msg_id + seq_no + length + inner
    return buf


def test_container_rejects_excessive_message_count():
    """M-4: Containers with more than MAXIMUM_LENGTH messages must be rejected."""
    data = _build_container_bytes(MessageContainer.MAXIMUM_LENGTH + 1)
    reader = BinaryReader(data)
    with pytest.raises(ValueError, match='max is'):
        MessageContainer.from_reader(reader)


def test_container_accepts_valid_count():
    """Valid containers should work fine."""
    data = _build_container_bytes(10)
    reader = BinaryReader(data)
    container = MessageContainer.from_reader(reader)
    assert len(container.messages) == 10
