"""
Tests for __main__._run() wiring:

B2: --first-run calls client.start() interactively, then exits 0.
C2: set_buffer_size is called with config.poll_buffer_size before client_holder.start.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


def make_args(
    config_path=None,
    first_run=False,
    migrate_to_encrypted=False,
    allow_plaintext=False,
):
    return SimpleNamespace(
        config=config_path,
        first_run=first_run,
        migrate_to_encrypted=migrate_to_encrypted,
        allow_plaintext=allow_plaintext,
    )


def make_config(tmp_path: Path):
    return SimpleNamespace(
        api_id=12345,
        api_hash="testhash",
        session_name="main",
        key_source="env",
        base_dir=tmp_path / "data",
        max_file_size_mb=100,
        allowed_mime_types=["application/pdf"],
        denied_mime_types=["video/*"],
        channels=[],
        read_chats=[100],
        write_chats=[100],
        ask_chats=[100],
        max_ops_per_minute=30,
        burst=5,
        concurrent_queue_wait_seconds=2.0,
        log_level="INFO",
        log_file=tmp_path / "bridge.log",
        ask_fallback="strict",
        fallback_window_seconds=120,
        poll_buffer_size=512,
        session_path=tmp_path / "main.session",
    )


class TestFirstRunPath:
    """B2: --first-run triggers interactive login and exits 0."""

    @pytest.mark.asyncio
    async def test_first_run_calls_client_start_and_returns_0(self, tmp_path):
        """--first-run must await client.start() and return exit code 0."""
        from mcp_bridge.__main__ import _run

        config = make_config(tmp_path)
        config.base_dir.mkdir(parents=True, exist_ok=True)

        mock_client = AsyncMock()
        mock_client.start = AsyncMock()
        mock_client.disconnect = AsyncMock()

        mock_session = MagicMock()

        args = make_args(first_run=True)

        with patch("mcp_bridge.__main__.Path") as MockPath, \
             patch("mcp_bridge.config.load_config", return_value=config), \
             patch("mcp_bridge.logging_setup.setup_logging"), \
             patch("mcp_bridge.session.encrypted_sqlite.EncryptedSQLiteSession",
                   return_value=mock_session), \
             patch("telethon.TelegramClient", return_value=mock_client):

            exit_code = await _run(args)

        assert exit_code == 0, f"Expected exit 0 but got {exit_code}"
        mock_client.start.assert_awaited_once()
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_run_does_not_start_mcp_server(self, tmp_path):
        """--first-run must NOT proceed to run_server."""
        from mcp_bridge.__main__ import _run

        config = make_config(tmp_path)
        config.base_dir.mkdir(parents=True, exist_ok=True)

        mock_client = AsyncMock()
        mock_client.start = AsyncMock()
        mock_client.disconnect = AsyncMock()

        args = make_args(first_run=True)

        run_server_called = False

        async def fake_run_server(*a, **kw):
            nonlocal run_server_called
            run_server_called = True

        with patch("mcp_bridge.config.load_config", return_value=config), \
             patch("mcp_bridge.logging_setup.setup_logging"), \
             patch("mcp_bridge.session.encrypted_sqlite.EncryptedSQLiteSession",
                   return_value=MagicMock()), \
             patch("telethon.TelegramClient", return_value=mock_client), \
             patch("mcp_bridge.server.run_server", side_effect=fake_run_server):

            exit_code = await _run(args)

        assert exit_code == 0
        assert not run_server_called, "run_server must NOT be called during --first-run"


class TestPollBufferSizeWiring:
    """C2: set_buffer_size is called with config.poll_buffer_size before client start."""

    @pytest.mark.asyncio
    async def test_set_buffer_size_called_with_config_value(self, tmp_path):
        """set_buffer_size(config.poll_buffer_size) is invoked during _run()."""
        from mcp_bridge.__main__ import _run
        from mcp_bridge.tools import poll

        config = make_config(tmp_path)
        config.base_dir.mkdir(parents=True, exist_ok=True)
        # Use a distinctive non-default value
        config.poll_buffer_size = 512

        args = make_args()

        recorded_sizes = []

        original_set = poll.set_buffer_size

        def tracking_set_buffer_size(size):
            recorded_sizes.append(size)
            original_set(size)

        mock_correlation = MagicMock()
        mock_correlation.open = MagicMock()
        mock_correlation.close = MagicMock()
        mock_correlation.recover_on_startup = MagicMock(return_value={})

        async def fake_client_start(*a, **kw):
            pass

        async def fake_run_server(*a, **kw):
            pass

        async def fake_stop():
            pass

        with patch("mcp_bridge.config.load_config", return_value=config), \
             patch("mcp_bridge.logging_setup.setup_logging"), \
             patch("mcp_bridge.tools.poll.set_buffer_size",
                   side_effect=tracking_set_buffer_size), \
             patch("mcp_bridge.correlation.Correlation", return_value=mock_correlation), \
             patch("mcp_bridge.client_holder.start", side_effect=fake_client_start), \
             patch("mcp_bridge.client_holder.stop", side_effect=fake_stop), \
             patch("mcp_bridge.client_holder.client", return_value=MagicMock()), \
             patch("mcp_bridge.server.run_server", side_effect=fake_run_server):

            exit_code = await _run(args)

        assert exit_code == 0
        assert 512 in recorded_sizes, (
            f"set_buffer_size was not called with 512; calls: {recorded_sizes}"
        )

    @pytest.mark.asyncio
    async def test_set_buffer_size_called_before_client_start(self, tmp_path):
        """set_buffer_size must be called BEFORE client_holder.start()."""
        from mcp_bridge.__main__ import _run
        from mcp_bridge.tools import poll

        config = make_config(tmp_path)
        config.base_dir.mkdir(parents=True, exist_ok=True)
        config.poll_buffer_size = 999

        args = make_args()

        call_order = []

        def tracking_set_buffer_size(size):
            call_order.append(("set_buffer_size", size))

        mock_correlation = MagicMock()
        mock_correlation.open = MagicMock()
        mock_correlation.close = MagicMock()
        mock_correlation.recover_on_startup = MagicMock(return_value={})

        async def tracking_client_start(*a, **kw):
            call_order.append(("client_start",))

        async def fake_run_server(*a, **kw):
            pass

        async def fake_stop():
            pass

        with patch("mcp_bridge.config.load_config", return_value=config), \
             patch("mcp_bridge.logging_setup.setup_logging"), \
             patch("mcp_bridge.tools.poll.set_buffer_size",
                   side_effect=tracking_set_buffer_size), \
             patch("mcp_bridge.correlation.Correlation", return_value=mock_correlation), \
             patch("mcp_bridge.client_holder.start",
                   side_effect=tracking_client_start), \
             patch("mcp_bridge.client_holder.stop", side_effect=fake_stop), \
             patch("mcp_bridge.client_holder.client", return_value=MagicMock()), \
             patch("mcp_bridge.server.run_server", side_effect=fake_run_server):

            await _run(args)

        # set_buffer_size must appear before client_start in the call_order
        buffer_idx = next(
            (i for i, c in enumerate(call_order) if c[0] == "set_buffer_size"), None
        )
        start_idx = next(
            (i for i, c in enumerate(call_order) if c[0] == "client_start"), None
        )
        assert buffer_idx is not None, "set_buffer_size was not called"
        assert start_idx is not None, "client_holder.start was not called"
        assert buffer_idx < start_idx, (
            f"set_buffer_size (idx={buffer_idx}) must be called before "
            f"client_holder.start (idx={start_idx}); full order: {call_order}"
        )
