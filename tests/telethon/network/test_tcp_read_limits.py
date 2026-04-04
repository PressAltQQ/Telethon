def test_abridged_has_max_packet_size():
    from telethon.network.connection.tcpabridged import MAX_PACKET_SIZE
    assert MAX_PACKET_SIZE == 2 * 1024 * 1024

def test_full_has_max_packet_size():
    from telethon.network.connection.tcpfull import MAX_PACKET_SIZE
    assert MAX_PACKET_SIZE == 2 * 1024 * 1024

def test_http_has_max_packet_size():
    from telethon.network.connection.http import MAX_PACKET_SIZE
    assert MAX_PACKET_SIZE == 2 * 1024 * 1024
