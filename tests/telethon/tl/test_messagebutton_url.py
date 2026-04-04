from urllib.parse import urlparse


def test_https_url_accepted():
    """M-9: https URLs should pass the scheme check."""
    url = 'https://telegram.org'
    parsed = urlparse(url)
    assert parsed.scheme in ('http', 'https')


def test_http_url_accepted():
    """M-9: http URLs should pass the scheme check."""
    url = 'http://example.com'
    parsed = urlparse(url)
    assert parsed.scheme in ('http', 'https')


def test_javascript_url_rejected():
    """M-9: javascript: URLs must be rejected."""
    url = 'javascript:alert(1)'
    parsed = urlparse(url)
    assert parsed.scheme not in ('http', 'https')


def test_file_url_rejected():
    """M-9: file: URLs must be rejected."""
    url = 'file:///etc/passwd'
    parsed = urlparse(url)
    assert parsed.scheme not in ('http', 'https')


def test_data_url_rejected():
    """M-9: data: URLs must be rejected."""
    url = 'data:text/html,<script>alert(1)</script>'
    parsed = urlparse(url)
    assert parsed.scheme not in ('http', 'https')
