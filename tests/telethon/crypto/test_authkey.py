import inspect
from telethon.crypto.authkey import AuthKey

def test_authkey_eq_uses_constant_time_comparison():
    source = inspect.getsource(AuthKey.__eq__)
    assert 'compare_digest' in source, "AuthKey.__eq__ must use hmac.compare_digest"
