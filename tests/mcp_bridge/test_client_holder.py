"""Tests for mcp_bridge/client_holder.py."""
import asyncio
import dataclasses
import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
import pytest_asyncio

from mcp_bridge.errors import SessionLockedError


@dataclasses.dataclass
class FakeConfig:
    api_id: int = 12345
    api_hash: str = "abc123"
    session_name: str = "test_session"
    session_path: Path = None
    base_dir: Path = Path("/tmp")


class TestSingleInstanceLock:
    @pytest.mark.asyncio
    async def test_flock_acquired_on_start(self, tmp_path):
        """start() should acquire fcntl lock on {session_path}.lock."""
        import mcp_bridge.client_holder as ch

        session_path = tmp_path / "test.session"
        cfg = FakeConfig(session_path=session_path)

        # Mock TelegramClient and EncryptedSQLiteSession so no real network
        mock_client = mock.AsyncMock()
        mock_session = mock.MagicMock()

        with (
            mock.patch("mcp_bridge.client_holder.EncryptedSQLiteSession", return_value=mock_session),
            mock.patch("mcp_bridge.client_holder.TelegramClient", return_value=mock_client),
        ):
            await ch.start(cfg)
            lock_path = tmp_path / "test.session.lock"
            assert lock_path.exists()
            assert ch._lock_fd is not None
            await ch.stop()

    @pytest.mark.asyncio
    async def test_second_start_raises_session_locked_with_pid(self, tmp_path):
        """Second instance reads PID from lockfile and raises SessionLockedError."""
        import mcp_bridge.client_holder as ch

        session_path = tmp_path / "test.session"
        cfg = FakeConfig(session_path=session_path)

        mock_client = mock.AsyncMock()
        mock_session = mock.MagicMock()

        with (
            mock.patch("mcp_bridge.client_holder.EncryptedSQLiteSession", return_value=mock_session),
            mock.patch("mcp_bridge.client_holder.TelegramClient", return_value=mock_client),
        ):
            await ch.start(cfg)
            first_pid = os.getpid()

            try:
                with pytest.raises(SessionLockedError) as exc_info:
                    await ch.start(cfg)
                assert exc_info.value.holder_pid == first_pid
            finally:
                await ch.stop()

    @pytest.mark.asyncio
    async def test_stop_releases_lock(self, tmp_path):
        """After stop(), lock should be released (second start should succeed)."""
        import mcp_bridge.client_holder as ch

        session_path = tmp_path / "test.session"
        cfg = FakeConfig(session_path=session_path)

        mock_client = mock.AsyncMock()
        mock_session = mock.MagicMock()

        with (
            mock.patch("mcp_bridge.client_holder.EncryptedSQLiteSession", return_value=mock_session),
            mock.patch("mcp_bridge.client_holder.TelegramClient", return_value=mock_client),
        ):
            await ch.start(cfg)
            await ch.stop()
            # Should succeed without raising
            await ch.start(cfg)
            await ch.stop()

    @pytest.mark.asyncio
    async def test_lockfile_created_with_restricted_perms(self, tmp_path):
        """Lockfile should be created with 0o600 permissions."""
        import mcp_bridge.client_holder as ch

        session_path = tmp_path / "test.session"
        cfg = FakeConfig(session_path=session_path)

        mock_client = mock.AsyncMock()
        mock_session = mock.MagicMock()

        with (
            mock.patch("mcp_bridge.client_holder.EncryptedSQLiteSession", return_value=mock_session),
            mock.patch("mcp_bridge.client_holder.TelegramClient", return_value=mock_client),
        ):
            await ch.start(cfg)
            lock_path = tmp_path / "test.session.lock"
            mode = oct(lock_path.stat().st_mode & 0o777)
            await ch.stop()

        assert mode == "0o600"


class TestWin32Guard:
    def test_win32_raises_import_error(self, monkeypatch):
        """Importing client_holder on win32 should raise ImportError."""
        monkeypatch.setattr(sys, "platform", "win32")

        # Remove from sys.modules to force re-import
        if "mcp_bridge.client_holder" in sys.modules:
            del sys.modules["mcp_bridge.client_holder"]

        with pytest.raises(ImportError, match="Unix-only"):
            import mcp_bridge.client_holder  # noqa: F401

    def test_non_win32_imports_fine(self, monkeypatch):
        """On non-win32, import should succeed."""
        monkeypatch.setattr(sys, "platform", "linux")

        if "mcp_bridge.client_holder" in sys.modules:
            del sys.modules["mcp_bridge.client_holder"]

        import mcp_bridge.client_holder  # noqa: F401

        # Restore to sys.modules state (will be re-imported correctly after test)
        del sys.modules["mcp_bridge.client_holder"]
