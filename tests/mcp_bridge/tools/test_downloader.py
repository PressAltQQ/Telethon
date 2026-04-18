"""Tests for mcp_bridge/tools/downloader.py (integration-style, mocked TelegramClient)."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest import mock

import pytest
import pytest_asyncio

from mcp_bridge.errors import (
    DownloadBusyError,
    FileTooLargeError,
    MessageNotFoundError,
    NotWhitelistedError,
    UnsupportedMimeError,
)
from mcp_bridge.tools.downloader import (
    _download_semaphore,
    download_file,
    list_channel_files,
)


@dataclasses.dataclass
class FakeConfig:
    channels: List[int] = dataclasses.field(default_factory=lambda: [-100123])
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
    concurrent_queue_wait_seconds: float = 2.0
    base_dir: Path = dataclasses.field(default_factory=lambda: Path("/tmp"))


def make_fake_attr(file_name: str):
    attr = mock.MagicMock()
    attr.file_name = file_name
    return attr


def make_fake_doc(size: int, mime: str, file_name: str):
    doc = mock.MagicMock()
    doc.size = size
    doc.mime_type = mime
    doc.attributes = [make_fake_attr(file_name)]
    return doc


def make_fake_message(msg_id: int, doc=None, date=None):
    msg = mock.MagicMock()
    msg.id = msg_id
    msg.document = doc
    msg.date = date or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return msg


def make_async_iter(items):
    """Create an async generator from a list."""
    async def _gen():
        for item in items:
            yield item
    return _gen()


class TestListChannelFiles:
    @pytest.mark.asyncio
    async def test_empty_channel_returns_empty_list(self, tmp_path):
        cfg = FakeConfig(channels=[-100123], base_dir=tmp_path)
        client = mock.MagicMock()
        client.iter_messages.return_value = make_async_iter([])

        result = await list_channel_files(client, cfg, -100123)
        assert result == []

    @pytest.mark.asyncio
    async def test_not_whitelisted_raises(self, tmp_path):
        cfg = FakeConfig(channels=[-100123], base_dir=tmp_path)
        client = mock.MagicMock()

        with pytest.raises(NotWhitelistedError):
            await list_channel_files(client, cfg, -999999)

    @pytest.mark.asyncio
    async def test_returns_metadata_for_docs(self, tmp_path):
        cfg = FakeConfig(
            channels=[-100123],
            allowed_mime_types=["application/pdf"],
            denied_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(1024, "application/pdf", "doc.pdf")
        msg = make_fake_message(101, doc=doc)
        msg2 = make_fake_message(102, doc=None)  # no document

        client = mock.MagicMock()
        client.iter_messages.return_value = make_async_iter([msg, msg2])

        result = await list_channel_files(client, cfg, -100123)
        assert len(result) == 1
        assert result[0]["message_id"] == 101
        assert result[0]["file_name"] == "doc.pdf"
        assert result[0]["size"] == 1024
        assert result[0]["mime_type"] == "application/pdf"

    @pytest.mark.asyncio
    async def test_filters_out_denied_mime(self, tmp_path):
        cfg = FakeConfig(channels=[-100123], denied_mime_types=["video/*"], base_dir=tmp_path)
        doc = make_fake_doc(1024, "video/mp4", "video.mp4")
        msg = make_fake_message(101, doc=doc)

        client = mock.MagicMock()
        client.iter_messages.return_value = make_async_iter([msg])

        result = await list_channel_files(client, cfg, -100123)
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_out_oversized_files(self, tmp_path):
        cfg = FakeConfig(
            channels=[-100123],
            max_file_size_mb=1,
            allowed_mime_types=[],
            denied_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(2 * 1024 * 1024, "application/pdf", "big.pdf")
        msg = make_fake_message(101, doc=doc)

        client = mock.MagicMock()
        client.iter_messages.return_value = make_async_iter([msg])

        result = await list_channel_files(client, cfg, -100123)
        assert result == []

    @pytest.mark.asyncio
    async def test_max_limit_1000(self, tmp_path):
        """limit should be capped at 1000."""
        cfg = FakeConfig(channels=[-100123], base_dir=tmp_path)
        client = mock.MagicMock()
        client.iter_messages.return_value = make_async_iter([])

        await list_channel_files(client, cfg, -100123, limit=9999)
        # The iter_messages call should use limit=1000
        call_kwargs = client.iter_messages.call_args
        assert call_kwargs[1].get("limit") == 1000 or call_kwargs[0][1:] == (1000,) \
            or call_kwargs.kwargs.get("limit") == 1000


class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_not_whitelisted_raises(self, tmp_path):
        cfg = FakeConfig(channels=[-100123], base_dir=tmp_path)
        client = mock.AsyncMock()

        with pytest.raises(NotWhitelistedError):
            await download_file(client, cfg, -999999, 101)

    @pytest.mark.asyncio
    async def test_download_creates_file(self, tmp_path):
        """download_file should create the file and return its path."""
        cfg = FakeConfig(
            channels=[-100123],
            allowed_mime_types=["application/pdf"],
            denied_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(100, "application/pdf", "test.pdf")
        msg = make_fake_message(101, doc=doc)

        async def fake_download(m, file=None):
            # Write some bytes to the temp file
            Path(file).write_bytes(b"PDF content here")
            return file

        client = mock.AsyncMock()
        client.get_messages = mock.AsyncMock(return_value=msg)
        client.download_media = fake_download

        result = await download_file(client, cfg, -100123, 101)
        assert result["already_downloaded"] is False
        assert Path(result["local_path"]).exists()
        assert "sha256" in result
        assert "size" in result

    @pytest.mark.asyncio
    async def test_idempotent_second_call_returns_already_downloaded(self, tmp_path):
        """Second call with matching sha256 returns already_downloaded=True."""
        cfg = FakeConfig(
            channels=[-100123],
            allowed_mime_types=["application/pdf"],
            denied_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(100, "application/pdf", "test.pdf")
        msg = make_fake_message(101, doc=doc)

        async def fake_download(m, file=None):
            Path(file).write_bytes(b"Identical content")
            return file

        client = mock.AsyncMock()
        client.get_messages = mock.AsyncMock(return_value=msg)
        client.download_media = fake_download

        result1 = await download_file(client, cfg, -100123, 101)
        assert result1["already_downloaded"] is False

        result2 = await download_file(client, cfg, -100123, 101)
        assert result2["already_downloaded"] is True
        assert result2["sha256"] == result1["sha256"]

    @pytest.mark.asyncio
    async def test_file_too_large_raises(self, tmp_path):
        cfg = FakeConfig(
            channels=[-100123],
            max_file_size_mb=1,
            allowed_mime_types=[],
            denied_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(2 * 1024 * 1024, "application/pdf", "big.pdf")
        msg = make_fake_message(101, doc=doc)

        client = mock.AsyncMock()
        client.get_messages = mock.AsyncMock(return_value=msg)

        with pytest.raises(FileTooLargeError):
            await download_file(client, cfg, -100123, 101)

    @pytest.mark.asyncio
    async def test_denied_mime_raises(self, tmp_path):
        cfg = FakeConfig(
            channels=[-100123],
            denied_mime_types=["video/*"],
            allowed_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(100, "video/mp4", "movie.mp4")
        msg = make_fake_message(101, doc=doc)

        client = mock.AsyncMock()
        client.get_messages = mock.AsyncMock(return_value=msg)

        with pytest.raises(UnsupportedMimeError):
            await download_file(client, cfg, -100123, 101)

    @pytest.mark.asyncio
    async def test_mid_batch_revocation_returns_not_whitelisted(self, tmp_path):
        """When batch_cursor is passed and channel not whitelisted, returns NOT_WHITELISTED
        with already_downloaded_count and pending_count."""
        cfg = FakeConfig(channels=[], base_dir=tmp_path)  # empty whitelist

        client = mock.AsyncMock()

        with pytest.raises(NotWhitelistedError) as exc_info:
            await download_file(
                client, cfg, -100123, 101,
                batch_cursor={"already_downloaded_count": 5, "pending_count": 10}
            )

        exc = exc_info.value
        assert exc.already_downloaded_count == 5
        assert exc.pending_count == 10

    @pytest.mark.asyncio
    async def test_concurrency_semaphore_second_call_raises_download_busy(self, tmp_path):
        """When semaphore is held, second concurrent call raises DownloadBusyError after timeout."""
        # Reset semaphore to known state
        import mcp_bridge.tools.downloader as mod
        # Ensure semaphore starts at 1
        while mod._download_semaphore._value < 1:
            mod._download_semaphore.release()

        cfg = FakeConfig(
            channels=[-100123],
            allowed_mime_types=["application/pdf"],
            denied_mime_types=[],
            concurrent_queue_wait_seconds=0.05,  # very short timeout for test speed
            base_dir=tmp_path,
        )

        acquired = asyncio.Event()
        release_event = asyncio.Event()

        async def slow_download(m, file=None):
            Path(file).write_bytes(b"slow content")
            return file

        async def slow_get_messages(channel_id, ids=None):
            doc = make_fake_doc(100, "application/pdf", "slow.pdf")
            msg = make_fake_message(ids, doc=doc)
            # Signal that the first download has started
            acquired.set()
            # Wait for release signal
            await release_event.wait()
            return msg

        client = mock.AsyncMock()
        client.get_messages = slow_get_messages
        client.download_media = slow_download

        # Start first download in background
        task1 = asyncio.create_task(download_file(client, cfg, -100123, 101))
        # Wait for first download to acquire semaphore
        await asyncio.wait_for(acquired.wait(), timeout=2.0)

        # Second download should timeout waiting for semaphore
        with pytest.raises(DownloadBusyError):
            await download_file(client, cfg, -100123, 102)

        # Release the first download
        release_event.set()
        await task1

    @pytest.mark.asyncio
    async def test_sc1_no_auth_key_in_response(self, tmp_path):
        """SC1: auth_key bytes must never appear in the returned dict."""
        cfg = FakeConfig(
            channels=[-100123],
            allowed_mime_types=["application/pdf"],
            denied_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(100, "application/pdf", "secret.pdf")
        msg = make_fake_message(101, doc=doc)

        # Inject a fake auth_key-like bytes object into the message mock
        auth_key_bytes = b"\x01\x02\x03\x04" * 32  # 128 bytes, like a real auth_key
        msg.auth_key = auth_key_bytes  # attribute that shouldn't leak

        async def fake_download(m, file=None):
            Path(file).write_bytes(b"content")
            return file

        client = mock.AsyncMock()
        client.get_messages = mock.AsyncMock(return_value=msg)
        client.download_media = fake_download

        result = await download_file(client, cfg, -100123, 101)

        # SC1: Verify only expected keys are present — no auth_key, no session bytes
        expected_keys = {"local_path", "already_downloaded", "size", "sha256"}
        assert set(result.keys()) == expected_keys
        # None of the values should be the auth_key bytes
        for v in result.values():
            assert v != auth_key_bytes, "auth_key bytes must not appear in result values"

    @pytest.mark.asyncio
    async def test_message_not_found_raises(self, tmp_path):
        cfg = FakeConfig(channels=[-100123], base_dir=tmp_path)
        client = mock.AsyncMock()
        client.get_messages = mock.AsyncMock(return_value=None)

        with pytest.raises(MessageNotFoundError):
            await download_file(client, cfg, -100123, 999)

    @pytest.mark.asyncio
    async def test_sc1_auth_key_never_leaks_across_tool_surface(self, tmp_path):
        """SC1: auth_key bytes must never appear in any serialized payload across multiple call paths."""
        import json

        auth_key_bytes = b"\x01\x02\x03\x04" * 32  # 128 bytes, like a real auth_key

        async def fake_download(m, file=None):
            Path(file).write_bytes(b"content")
            return file

        # --- call path 1: download_file ---
        cfg = FakeConfig(
            channels=[-100123],
            allowed_mime_types=["application/pdf"],
            denied_mime_types=[],
            base_dir=tmp_path,
        )
        doc = make_fake_doc(100, "application/pdf", "secret.pdf")
        msg = make_fake_message(101, doc=doc)
        msg.auth_key = auth_key_bytes

        client = mock.AsyncMock()
        client.get_messages = mock.AsyncMock(return_value=msg)
        client.download_media = fake_download

        result1 = await download_file(client, cfg, -100123, 101)
        payload1 = json.dumps(result1)
        assert auth_key_bytes.hex() not in payload1, "auth_key hex must not appear in download_file payload"
        assert auth_key_bytes.decode("latin-1", errors="replace") not in payload1

        # --- call path 2: list_channel_files ---
        doc2 = make_fake_doc(100, "application/pdf", "doc2.pdf")
        msg2 = make_fake_message(102, doc=doc2)
        msg2.auth_key = auth_key_bytes
        msg2.date = msg2.date  # ensure it's set

        client2 = mock.MagicMock()
        client2.iter_messages.return_value = make_async_iter([msg2])

        result2 = await list_channel_files(client2, cfg, -100123)
        payload2 = json.dumps(result2)
        assert auth_key_bytes.hex() not in payload2, "auth_key hex must not appear in list_channel_files payload"


class TestListChannelFilesWhitelistRecheck:
    @pytest.mark.asyncio
    async def test_whitelist_revoked_mid_iteration_returns_partial(self, tmp_path):
        """If whitelist is mutated mid-iteration, list_channel_files returns partial results."""
        cfg = FakeConfig(channels=[-100123], base_dir=tmp_path)

        doc1 = make_fake_doc(100, "application/pdf", "first.pdf")
        msg1 = make_fake_message(101, doc=doc1)

        doc2 = make_fake_doc(100, "application/pdf", "second.pdf")
        msg2 = make_fake_message(102, doc=doc2)

        call_count = 0

        async def fake_iter(channel_id, limit=100):
            nonlocal call_count
            call_count += 1
            yield msg1
            # Revoke whitelist after first message
            cfg.channels = []
            yield msg2

        client = mock.MagicMock()
        client.iter_messages = fake_iter

        result = await list_channel_files(client, cfg, -100123)
        # Should have returned only msg1 before whitelist was revoked
        assert len(result) == 1
        assert result[0]["message_id"] == 101
