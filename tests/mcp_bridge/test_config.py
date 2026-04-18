"""Tests for mcp_bridge/config.py."""
import os
import stat
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from mcp_bridge.config import load_config
from mcp_bridge.errors import ConfigInvalidError


VALID_TOML = textwrap.dedent("""\
    [telegram]
    api_id = 12345
    api_hash = "abc123"
    session_name = "main"

    [session]
    key_source = "keyring"

    [downloader]
    base_dir = "/tmp/tg-downloads"
    max_file_size_mb = 100
    allowed_mime_types = ["application/pdf", "image/*"]
    denied_mime_types = ["video/*"]

    [whitelist]
    channels = [-100123]
    read_chats = [12345]
    write_chats = [12345]
    ask_chats = [12345]

    [rate_limit]
    max_ops_per_minute = 30
    burst = 5

    [logging]
    level = "INFO"
    file = "/tmp/bridge.log"
""")


def write_config(tmp_path: Path, content: str, mode: int = 0o600) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content)
    p.chmod(mode)
    return p


class TestValidLoad:
    def test_valid_toml_loads(self, tmp_path):
        p = write_config(tmp_path, VALID_TOML)
        cfg = load_config(p)
        assert cfg.api_id == 12345
        assert cfg.api_hash == "abc123"
        assert cfg.session_name == "main"
        assert cfg.key_source == "keyring"
        assert cfg.max_file_size_mb == 100
        assert cfg.channels == [-100123]
        assert cfg.read_chats == [12345]
        assert cfg.write_chats == [12345]
        assert cfg.ask_chats == [12345]
        assert cfg.max_ops_per_minute == 30
        assert cfg.burst == 5
        assert cfg.log_level == "INFO"

    def test_base_dir_is_path(self, tmp_path):
        p = write_config(tmp_path, VALID_TOML)
        cfg = load_config(p)
        assert isinstance(cfg.base_dir, Path)

    def test_log_file_is_path(self, tmp_path):
        p = write_config(tmp_path, VALID_TOML)
        cfg = load_config(p)
        assert isinstance(cfg.log_file, Path)


@pytest.mark.skipif(sys.platform == "win32", reason="Permission checks are Unix-only")
class TestPermissionCheck:
    def test_0o644_rejected(self, tmp_path):
        p = write_config(tmp_path, VALID_TOML, mode=0o644)
        with pytest.raises(ConfigInvalidError, match="0o6"):
            load_config(p)

    def test_0o640_rejected(self, tmp_path):
        p = write_config(tmp_path, VALID_TOML, mode=0o640)
        with pytest.raises(ConfigInvalidError):
            load_config(p)

    def test_0o600_accepted(self, tmp_path):
        p = write_config(tmp_path, VALID_TOML, mode=0o600)
        cfg = load_config(p)
        assert cfg.api_id == 12345


class TestEnvOverride:
    def test_api_hash_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELETHON_API_HASH", "env_hash_override")
        p = write_config(tmp_path, VALID_TOML)
        cfg = load_config(p)
        assert cfg.api_hash == "env_hash_override"

    def test_api_hash_from_file_when_env_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TELETHON_API_HASH", raising=False)
        p = write_config(tmp_path, VALID_TOML)
        cfg = load_config(p)
        assert cfg.api_hash == "abc123"


class TestMissingRequiredFields:
    def test_missing_telegram_table(self, tmp_path):
        toml = VALID_TOML.replace("[telegram]\napi_id = 12345\napi_hash = \"abc123\"\nsession_name = \"main\"\n\n", "")
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="telegram"):
            load_config(p)

    def test_missing_session_table(self, tmp_path):
        toml = VALID_TOML.replace("[session]\nkey_source = \"keyring\"\n\n", "")
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="session"):
            load_config(p)

    def test_missing_downloader_table(self, tmp_path):
        lines = [
            line for line in VALID_TOML.splitlines()
            if not line.startswith("[downloader]") and not any(
                line.startswith(k) for k in
                ["base_dir", "max_file_size", "allowed_mime", "denied_mime"]
            )
        ]
        toml = "\n".join(lines)
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="downloader"):
            load_config(p)

    def test_missing_whitelist_table(self, tmp_path):
        lines = [
            line for line in VALID_TOML.splitlines()
            if not line.startswith("[whitelist]") and not any(
                line.startswith(k) for k in
                ["channels", "read_chats", "write_chats", "ask_chats"]
            )
        ]
        toml = "\n".join(lines)
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="whitelist"):
            load_config(p)

    def test_missing_rate_limit_table(self, tmp_path):
        lines = [
            line for line in VALID_TOML.splitlines()
            if not line.startswith("[rate_limit]") and not any(
                line.startswith(k) for k in ["max_ops_per_minute", "burst"]
            )
        ]
        toml = "\n".join(lines)
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="rate_limit"):
            load_config(p)

    def test_missing_logging_table(self, tmp_path):
        lines = [
            line for line in VALID_TOML.splitlines()
            if not line.startswith("[logging]") and not any(
                line.startswith(k) for k in ["level", "file"]
            )
        ]
        toml = "\n".join(lines)
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="logging"):
            load_config(p)


class TestNFSCheck:
    def test_nfs_path_refused_linux(self, tmp_path, monkeypatch):
        """Mock /proc/mounts to show NFS mount covering session path."""
        # Use a path we control
        p = write_config(tmp_path, VALID_TOML.replace(
            'base_dir = "/tmp/tg-downloads"',
            f'base_dir = "{tmp_path}/downloads"'
        ))

        proc_mounts = f"server:/export {tmp_path}/downloads nfs rw 0 0\n"

        def fake_open(path, *args, **kwargs):
            if str(path) == "/proc/mounts":
                import io
                return io.StringIO(proc_mounts)
            return open(path, *args, **kwargs)

        monkeypatch.setattr(sys, "platform", "linux")
        with mock.patch("builtins.open", side_effect=fake_open):
            with pytest.raises(ConfigInvalidError, match="NFS"):
                load_config(p)

    def test_local_path_allowed(self, tmp_path):
        """Local path should not trigger NFS refusal."""
        p = write_config(tmp_path, VALID_TOML.replace(
            'base_dir = "/tmp/tg-downloads"',
            f'base_dir = "{tmp_path}"'
        ))
        # Should not raise
        cfg = load_config(p)
        assert cfg.api_id == 12345

    def test_nfs_path_refused_macos(self, tmp_path, monkeypatch):
        """Mock macOS 'mount' output to show NFS mount covering session path."""
        p = write_config(tmp_path, VALID_TOML.replace(
            'base_dir = "/tmp/tg-downloads"',
            f'base_dir = "{tmp_path}/downloads"'
        ))

        mount_output = f"server:/export on {tmp_path}/downloads (nfs, nodev, nosuid)\n"

        monkeypatch.setattr(sys, "platform", "darwin")

        import subprocess
        mock_result = mock.MagicMock()
        mock_result.stdout = mount_output

        with mock.patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ConfigInvalidError, match="NFS"):
                load_config(p)

    def test_prefix_match_does_not_false_positive_on_similar_name(self, tmp_path, monkeypatch):
        """/homer/foo must NOT be matched against mount_point /home (path-traversal guard)."""
        from mcp_bridge.config import _check_mounts_text

        # /home is an NFS mount, but /homer/foo is NOT under /home
        mounts_text = "server:/export /home nfs rw 0 0\n"
        # This must not raise — /homer/foo does not start with /home/
        _check_mounts_text("/homer/foo", mounts_text)  # should not raise


class TestRateLimitValidation:
    def test_zero_max_ops_per_minute_rejected(self, tmp_path):
        """max_ops_per_minute=0 must raise ConfigInvalidError."""
        toml = VALID_TOML.replace("max_ops_per_minute = 30", "max_ops_per_minute = 0")
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="max_ops_per_minute"):
            load_config(p)

    def test_negative_max_ops_per_minute_rejected(self, tmp_path):
        """max_ops_per_minute=-1 must raise ConfigInvalidError."""
        toml = VALID_TOML.replace("max_ops_per_minute = 30", "max_ops_per_minute = -1")
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="max_ops_per_minute"):
            load_config(p)

    def test_zero_burst_rejected(self, tmp_path):
        """burst=0 must raise ConfigInvalidError."""
        toml = VALID_TOML.replace("burst = 5", "burst = 0")
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="burst"):
            load_config(p)

    def test_negative_burst_rejected(self, tmp_path):
        """burst=-1 must raise ConfigInvalidError."""
        toml = VALID_TOML.replace("burst = 5", "burst = -1")
        p = write_config(tmp_path, toml)
        with pytest.raises(ConfigInvalidError, match="burst"):
            load_config(p)

    def test_valid_rate_limit_values_accepted(self, tmp_path):
        """max_ops_per_minute=1 and burst=1 are the minimum valid values."""
        toml = VALID_TOML.replace("max_ops_per_minute = 30", "max_ops_per_minute = 1")
        toml = toml.replace("burst = 5", "burst = 1")
        p = write_config(tmp_path, toml)
        cfg = load_config(p)
        assert cfg.max_ops_per_minute == 1
        assert cfg.burst == 1
