"""Tests for mcp_bridge/filters.py."""
import dataclasses
from typing import List

import pytest

from mcp_bridge.errors import FileTooLargeError, UnsupportedMimeError
from mcp_bridge.filters import (
    channel_slug,
    check_file,
    is_whitelisted,
    safe_name,
)


@dataclasses.dataclass
class FakeConfig:
    channels: List[int] = dataclasses.field(default_factory=list)
    read_chats: List[int] = dataclasses.field(default_factory=list)
    write_chats: List[int] = dataclasses.field(default_factory=list)
    ask_chats: List[int] = dataclasses.field(default_factory=list)
    max_file_size_mb: int = 100
    allowed_mime_types: List[str] = dataclasses.field(
        default_factory=lambda: ["application/pdf", "image/*", "document/*"]
    )
    denied_mime_types: List[str] = dataclasses.field(
        default_factory=lambda: ["video/*"]
    )


class TestIsWhitelisted:
    def test_channels_present(self):
        cfg = FakeConfig(channels=[-100123, -100456])
        assert is_whitelisted(-100123, "channels", cfg) is True

    def test_channels_absent(self):
        cfg = FakeConfig(channels=[-100123])
        assert is_whitelisted(-100999, "channels", cfg) is False

    def test_read_chats_present(self):
        cfg = FakeConfig(read_chats=[12345])
        assert is_whitelisted(12345, "read_chats", cfg) is True

    def test_read_chats_absent(self):
        cfg = FakeConfig(read_chats=[12345])
        assert is_whitelisted(99999, "read_chats", cfg) is False

    def test_write_chats_present(self):
        cfg = FakeConfig(write_chats=[555])
        assert is_whitelisted(555, "write_chats", cfg) is True

    def test_write_chats_absent(self):
        cfg = FakeConfig(write_chats=[555])
        assert is_whitelisted(666, "write_chats", cfg) is False

    def test_ask_chats_present(self):
        cfg = FakeConfig(ask_chats=[777])
        assert is_whitelisted(777, "ask_chats", cfg) is True

    def test_ask_chats_absent(self):
        cfg = FakeConfig(ask_chats=[777])
        assert is_whitelisted(888, "ask_chats", cfg) is False

    def test_unknown_list_name_returns_false(self):
        cfg = FakeConfig(channels=[1])
        assert is_whitelisted(1, "nonexistent_list", cfg) is False

    def test_empty_list_is_false(self):
        cfg = FakeConfig(channels=[])
        assert is_whitelisted(1, "channels", cfg) is False


class TestCheckFile:
    def test_passes_within_size_limit(self):
        cfg = FakeConfig(max_file_size_mb=10)
        # 10 MB - 1 byte: should pass
        check_file(10 * 1024 * 1024 - 1, "application/pdf", cfg)

    def test_fails_over_size_limit(self):
        cfg = FakeConfig(max_file_size_mb=10)
        with pytest.raises(FileTooLargeError):
            check_file(10 * 1024 * 1024 + 1, "application/pdf", cfg)

    def test_exact_size_limit_fails(self):
        cfg = FakeConfig(max_file_size_mb=10)
        with pytest.raises(FileTooLargeError):
            check_file(10 * 1024 * 1024 + 1, "application/pdf", cfg)

    def test_denied_mime_raises(self):
        cfg = FakeConfig()
        with pytest.raises(UnsupportedMimeError):
            check_file(100, "video/mp4", cfg)

    def test_denied_wildcard_matches(self):
        cfg = FakeConfig(denied_mime_types=["video/*"])
        with pytest.raises(UnsupportedMimeError):
            check_file(100, "video/avi", cfg)

    def test_denied_takes_precedence_over_allowed(self):
        cfg = FakeConfig(
            allowed_mime_types=["video/*"],
            denied_mime_types=["video/*"],
        )
        with pytest.raises(UnsupportedMimeError):
            check_file(100, "video/mp4", cfg)

    def test_allowed_mime_passes(self):
        cfg = FakeConfig(allowed_mime_types=["application/pdf"], denied_mime_types=[])
        check_file(100, "application/pdf", cfg)

    def test_not_in_allowed_raises(self):
        cfg = FakeConfig(
            allowed_mime_types=["application/pdf"],
            denied_mime_types=[],
        )
        with pytest.raises(UnsupportedMimeError):
            check_file(100, "application/zip", cfg)

    def test_wildcard_allowed(self):
        cfg = FakeConfig(allowed_mime_types=["image/*"], denied_mime_types=[])
        check_file(100, "image/jpeg", cfg)

    def test_empty_allowed_list_passes_all(self):
        """If allowed_mime_types is empty, no restriction (all pass unless denied)."""
        cfg = FakeConfig(allowed_mime_types=[], denied_mime_types=[])
        check_file(100, "application/octet-stream", cfg)


class TestSafeName:
    def test_plain_name_unchanged(self):
        assert safe_name("hello.txt") == "hello.txt"

    def test_slash_replaced(self):
        assert "/" not in safe_name("dir/file.txt")
        assert safe_name("dir/file.txt") == "dir_file.txt"

    def test_backslash_replaced(self):
        assert "\\" not in safe_name("dir\\file.txt")
        assert safe_name("dir\\file.txt") == "dir_file.txt"

    def test_null_bytes_stripped(self):
        assert "\x00" not in safe_name("file\x00.txt")

    def test_nfkc_normalized(self):
        # ligature fi → fi
        name = safe_name("\ufb01le.txt")  # ﬁ ligature
        assert "\ufb01" not in name

    def test_windows_reserved_con(self):
        result = safe_name("CON")
        assert result.startswith("_reserved_")

    def test_windows_reserved_nul(self):
        result = safe_name("NUL.txt")
        assert result.startswith("_reserved_")

    def test_windows_reserved_com1(self):
        result = safe_name("COM1")
        assert result.startswith("_reserved_")

    def test_windows_reserved_case_insensitive(self):
        result = safe_name("con.txt")
        assert result.startswith("_reserved_")

    def test_truncation_at_120_chars(self):
        long_name = "a" * 200
        result = safe_name(long_name)
        assert len(result) <= 120

    def test_truncation_preserves_extension(self):
        long_name = "a" * 200 + ".pdf"
        result = safe_name(long_name)
        assert result.endswith(".pdf")
        assert len(result) <= 120

    def test_empty_string_returns_underscore(self):
        assert safe_name("") == "_"

    def test_only_separators_returns_underscore(self):
        result = safe_name("///")
        # slashes replaced with underscores
        assert "/" not in result

    def test_normal_filename_not_reserved(self):
        result = safe_name("document.pdf")
        assert result == "document.pdf"


class TestChannelSlug:
    def test_basic_slug(self):
        result = channel_slug(-100123, "My Channel")
        assert result == "-100123_My Channel"

    def test_no_name_uses_unnamed(self):
        result = channel_slug(-100123, None)
        assert result == "-100123_unnamed"

    def test_empty_string_name(self):
        result = channel_slug(-100123, "")
        assert result == "-100123_unnamed"

    def test_name_with_slash_sanitized(self):
        result = channel_slug(-100123, "my/channel")
        assert "/" not in result
        assert result.startswith("-100123_")

    def test_slug_contains_channel_id(self):
        result = channel_slug(99999, "test")
        assert "99999" in result
