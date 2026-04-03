def test_quart_example_no_hardcoded_secret():
    with open('telethon_examples/quart_login.py') as f:
        source = f.read()
    assert 'CHANGE THIS TO SOMETHING SECRET' not in source, \
        "quart_login.py still has a hardcoded secret key"

def test_quart_example_password_field_type():
    with open('telethon_examples/quart_login.py') as f:
        source = f.read()
    assert "type='text' placeholder='your password'" not in source, \
        "Password field must use type='password', not type='text'"
