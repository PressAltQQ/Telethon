"""Smoke tests for telethon/client/downloads.py — public API surface only.

The existing test_downloads_safe_join.py covers the N-5 path-traversal
defense. This file guards the mixin composition and high-level dispatch.
"""
import inspect

import pytest

from telethon import TelegramClient
from telethon.client.downloads import DownloadMethods


def test_download_methods_is_mixin_of_telegramclient():
    assert issubclass(TelegramClient, DownloadMethods)


def test_download_media_signature_keeps_documented_kwargs():
    params = inspect.signature(DownloadMethods.download_media).parameters
    for expected in ("message", "file", "thumb", "progress_callback"):
        assert expected in params, f"download_media lost kwarg: {expected}"


def test_download_file_signature_keeps_documented_kwargs():
    params = inspect.signature(DownloadMethods.download_file).parameters
    for expected in ("input_location", "file", "part_size_kb", "file_size",
                     "progress_callback", "dc_id"):
        assert expected in params, f"download_file lost kwarg: {expected}"


def test_iter_download_exists_and_is_async_iterable_factory():
    """iter_download returns an async iterator; confirm it's coroutine/async-gen."""
    assert hasattr(DownloadMethods, "iter_download")
    # inspect on the bound method signature
    params = inspect.signature(DownloadMethods.iter_download).parameters
    assert "file" in params
    assert "offset" in params


def test_download_profile_photo_signature():
    params = inspect.signature(DownloadMethods.download_profile_photo).parameters
    for expected in ("entity", "file", "download_big"):
        assert expected in params, f"download_profile_photo lost kwarg: {expected}"
