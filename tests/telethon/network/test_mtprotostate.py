import inspect
from telethon.network.mtprotostate import MTProtoState

def test_decrypt_uses_constant_time_comparison():
    source = inspect.getsource(MTProtoState.decrypt_message_data)
    assert 'compare_digest' in source, "decrypt_message_data must use hmac.compare_digest"
