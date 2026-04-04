from telethon.extensions.markdown import parse


def test_oversized_message_returns_unchanged():
    """L-9: Messages exceeding 8192 chars should bypass regex parsing."""
    huge = 'a' * 10000
    text, entities = parse(huge)
    assert text == huge
    assert entities == []
