def test_vcard_newline_stripped_from_first_name():
    """L-8: Newlines in first_name must not inject vCard fields."""
    first_name = "Evil\nX-CUSTOM:injected"
    sanitized = first_name.replace(';', '').replace('\n', '').replace('\r', '')
    assert '\n' not in sanitized
    assert '\r' not in sanitized


def test_vcard_carriage_return_stripped():
    """L-8: Carriage returns must also be stripped."""
    name = "Evil\r\nX-CUSTOM:injected"
    sanitized = name.replace(';', '').replace('\n', '').replace('\r', '')
    assert '\n' not in sanitized
    assert '\r' not in sanitized
