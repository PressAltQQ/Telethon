"""
Tests for _safe_join in telethon/client/downloads.py (N-5 defense-in-depth).
"""
import os
import pathlib

import pytest

from telethon.client.downloads import _safe_join


class TestSafeJoinTraversal:
    def test_dotdot_slash_traversal_confined(self, tmp_path):
        """../../etc/passwd: forward slashes are replaced with underscores,
        result is confined inside base_dir — no path escape."""
        # Path separators are stripped, so '../../etc/passwd' becomes '.._.._etc_passwd'
        # which is a normal filename inside base_dir.  No ValueError expected.
        result = _safe_join(tmp_path, "../../etc/passwd")
        assert str(result).startswith(str(tmp_path.resolve()))
        assert result.name == ".._.._etc_passwd"

    def test_absolute_path_is_confined(self, tmp_path):
        """An absolute path as the name gets path-sep stripped and confined."""
        # The '/' in the name gets replaced with '_', so it becomes safe.
        # But even if it looked like an absolute path, resolve() would keep it in base.
        result = _safe_join(tmp_path, "/etc/passwd")
        # After stripping '/', name becomes '_etc_passwd', which is inside base_dir.
        assert result.parent == tmp_path

    def test_double_dotdot_in_subdir(self, tmp_path):
        """../.. style: forward slash stripped → becomes relative-looking but still safe."""
        # After stripping '/', '../..' becomes '.._..', which is a valid filename.
        result = _safe_join(tmp_path, "../..")
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_backslash_traversal_sanitised(self, tmp_path):
        """Windows-style traversal via backslash is replaced with underscore."""
        result = _safe_join(tmp_path, "..\\..\\windows\\system32")
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_pure_dotdot_without_separator_confined(self, tmp_path):
        """A bare '..' name (no path separator) should be treated as a filename.

        On POSIX, base_dir / '..' resolves to the parent of base_dir, which IS
        a path escape.  _safe_join must catch this via the resolve() check.
        """
        # Note: pathlib.Path(tmp_path) / '..' resolves outside tmp_path.
        # _safe_join should detect and raise ValueError.
        with pytest.raises(ValueError, match="path escape"):
            _safe_join(tmp_path, "..")


class TestSafeJoinWindowsReservedNames:
    def test_con_rewritten(self, tmp_path):
        """CON.txt → _reserved_CON.txt (case-insensitive)."""
        result = _safe_join(tmp_path, "CON.txt")
        assert result.name == "_reserved_CON.txt"

    def test_con_lowercase_rewritten(self, tmp_path):
        """con.txt → _reserved_con.txt (case-insensitive stem match)."""
        result = _safe_join(tmp_path, "con.txt")
        assert result.name == "_reserved_con.txt"

    def test_nul_rewritten(self, tmp_path):
        """NUL → _reserved_NUL."""
        result = _safe_join(tmp_path, "NUL")
        assert result.name == "_reserved_NUL"

    def test_com1_rewritten(self, tmp_path):
        """COM1 → _reserved_COM1."""
        result = _safe_join(tmp_path, "COM1")
        assert result.name == "_reserved_COM1"

    def test_lpt9_rewritten(self, tmp_path):
        """LPT9.doc → _reserved_LPT9.doc."""
        result = _safe_join(tmp_path, "LPT9.doc")
        assert result.name == "_reserved_LPT9.doc"

    def test_normal_name_not_rewritten(self, tmp_path):
        """A normal filename is unchanged."""
        result = _safe_join(tmp_path, "report.pdf")
        assert result.name == "report.pdf"


class TestSafeJoinUnicode:
    def test_nfkc_normalization_applied(self, tmp_path):
        """Unicode NFD input is normalised to NFKC."""
        # NFD: 'e' + combining acute accent (U+0301) → NFC 'é' (U+00E9)
        # NFKC may further map compatibility characters, but NFKC of NFD is
        # equivalent to NFC for base+combining sequences.
        nfd_name = "caf\u0065\u0301.txt"   # cafe + combining acute = "café"
        result = _safe_join(tmp_path, nfd_name)
        # After NFKC: 'é' is U+00E9 (one char, not two).
        assert "\u0301" not in result.name  # combining accent stripped into composed form

    def test_fullwidth_chars_normalized(self, tmp_path):
        """Fullwidth Latin letters are mapped to ASCII equivalents under NFKC."""
        # NFKC normalises fullwidth 'Ａ' (U+FF21) to regular 'A'.
        result = _safe_join(tmp_path, "\uff21\uff42\uff43.txt")  # ＡＢＣ.txt
        assert "\uff21" not in result.name


class TestSafeJoinTruncation:
    def test_long_name_truncated_to_120_chars(self, tmp_path):
        """A 300-char name is truncated to 120 chars total."""
        long_name = "a" * 300 + ".txt"
        result = _safe_join(tmp_path, long_name)
        assert len(result.name) <= 120

    def test_extension_preserved_after_truncation(self, tmp_path):
        """The file extension is preserved after truncation."""
        long_name = "b" * 200 + ".pdf"
        result = _safe_join(tmp_path, long_name)
        assert result.name.endswith(".pdf")
        assert len(result.name) <= 120

    def test_exactly_120_chars_untouched(self, tmp_path):
        """A 120-char name is not truncated."""
        name = "c" * 116 + ".txt"  # 116 + 4 = 120 chars
        result = _safe_join(tmp_path, name)
        assert result.name == name

    def test_121_chars_truncated(self, tmp_path):
        """A 121-char name is truncated to 120."""
        name = "d" * 117 + ".txt"  # 117 + 4 = 121 chars
        result = _safe_join(tmp_path, name)
        assert len(result.name) == 120


class TestSafeJoinNullByte:
    def test_null_byte_stripped(self, tmp_path):
        """Null bytes in the filename are stripped."""
        result = _safe_join(tmp_path, "file\x00name.txt")
        assert "\x00" not in result.name

    def test_null_byte_in_middle(self, tmp_path):
        """Null bytes in the middle of a name are stripped."""
        result = _safe_join(tmp_path, "abc\x00def.txt")
        assert result.name == "abcdef.txt"


class TestSafeJoinHappyPath:
    def test_normal_filename_resolves_inside_base(self, tmp_path):
        """A clean filename resolves to a path inside base_dir."""
        result = _safe_join(tmp_path, "document.pdf")
        assert str(result).startswith(str(tmp_path.resolve()))
        assert result.name == "document.pdf"
