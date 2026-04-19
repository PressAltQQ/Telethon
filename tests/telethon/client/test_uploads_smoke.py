"""Smoke tests for telethon/client/uploads.py — public API surface only."""
import inspect

import pytest

from telethon import TelegramClient
from telethon.client.uploads import UploadMethods


def test_upload_methods_is_mixin_of_telegramclient():
    assert issubclass(TelegramClient, UploadMethods)


def test_send_file_signature_keeps_documented_kwargs():
    params = inspect.signature(UploadMethods.send_file).parameters
    for expected in ("entity", "file", "caption", "force_document", "thumb",
                     "buttons", "reply_to", "silent", "schedule",
                     "progress_callback", "parse_mode"):
        assert expected in params, f"send_file lost kwarg: {expected}"


def test_upload_file_signature_keeps_documented_kwargs():
    params = inspect.signature(UploadMethods.upload_file).parameters
    for expected in ("file", "part_size_kb", "file_name", "use_cache",
                     "progress_callback"):
        assert expected in params, f"upload_file lost kwarg: {expected}"


def test_send_file_is_coroutine_function():
    assert inspect.iscoroutinefunction(UploadMethods.send_file)


def test_upload_file_is_coroutine_function():
    assert inspect.iscoroutinefunction(UploadMethods.upload_file)
