from telethon.extensions.html import unparse
from telethon.tl.types import (
    MessageEntityUrl,
    MessageEntityEmail,
    MessageEntityPre,
)


def test_url_entity_escapes_html_in_href():
    """M-7: MessageEntityUrl must escape the URL text in href."""
    malicious_url = 'http://a" onmouseover="alert(1)'
    text = malicious_url
    entity = MessageEntityUrl(offset=0, length=len(text))
    result = unparse(text, [entity])
    # The double quote must be escaped to &quot; so it cannot break out of the attribute
    assert '" onmouseover="' not in result
    assert '&quot;' in result


def test_email_entity_escapes_html_in_href():
    """M-7: MessageEntityEmail must escape the email text in href."""
    malicious_email = 'user"onmouseover="alert(1)@evil.com'
    text = malicious_email
    entity = MessageEntityEmail(offset=0, length=len(text))
    result = unparse(text, [entity])
    # The double quote must be escaped so the href attribute cannot be broken out of
    assert '"onmouseover="' not in result
    assert '&quot;' in result


def test_pre_entity_escapes_language():
    """M-8: language attribute in <pre><code class='language-X'> must be escaped."""
    malicious_lang = "python' onclick='alert(1)"
    text = "print('hello')"
    entity = MessageEntityPre(offset=0, length=len(text), language=malicious_lang)
    result = unparse(text, [entity])
    # The single quote must be escaped so the class attribute cannot be broken out of
    assert "' onclick='" not in result
    assert '&#x27;' in result
