"""Tests for mcp_bridge/errors.py."""
import pytest

from mcp_bridge.errors import (
    AskOrphanedError,
    AskSendFailedError,
    AskTimeoutError,
    BridgeError,
    ChannelNotFoundError,
    ConfigInvalidError,
    FileTooLargeError,
    FloodWaitError,
    InternalError,
    KeyringUnavailableError,
    MessageNotFoundError,
    NotWhitelistedError,
    PollAlreadyActiveError,
    PollCursorLostError,
    RateLimitError,
    SessionLockedError,
    UnsupportedMimeError,
    to_error_response,
)


class TestErrorCodes:
    def test_flood_wait_code(self):
        assert FloodWaitError.CODE == "FLOOD_WAIT"

    def test_not_whitelisted_code(self):
        assert NotWhitelistedError.CODE == "NOT_WHITELISTED"

    def test_file_too_large_code(self):
        assert FileTooLargeError.CODE == "FILE_TOO_LARGE"

    def test_unsupported_mime_code(self):
        assert UnsupportedMimeError.CODE == "UNSUPPORTED_MIME"

    def test_channel_not_found_code(self):
        assert ChannelNotFoundError.CODE == "CHANNEL_NOT_FOUND"

    def test_message_not_found_code(self):
        assert MessageNotFoundError.CODE == "MESSAGE_NOT_FOUND"

    def test_ask_timeout_code(self):
        assert AskTimeoutError.CODE == "ASK_TIMEOUT"

    def test_ask_orphaned_code(self):
        assert AskOrphanedError.CODE == "ASK_ORPHANED"

    def test_ask_send_failed_code(self):
        assert AskSendFailedError.CODE == "ASK_SEND_FAILED"

    def test_session_locked_code(self):
        assert SessionLockedError.CODE == "SESSION_LOCKED"

    def test_keyring_unavailable_code(self):
        assert KeyringUnavailableError.CODE == "KEYRING_UNAVAILABLE"

    def test_poll_cursor_lost_code(self):
        assert PollCursorLostError.CODE == "POLL_CURSOR_LOST"

    def test_poll_already_active_code(self):
        assert PollAlreadyActiveError.CODE == "POLL_ALREADY_ACTIVE"

    def test_config_invalid_code(self):
        assert ConfigInvalidError.CODE == "CONFIG_INVALID"

    def test_rate_limit_code(self):
        assert RateLimitError.CODE == "RATE_LIMIT"

    def test_internal_code(self):
        assert InternalError.CODE == "INTERNAL"

    def test_all_are_bridge_errors(self):
        for cls in [
            FloodWaitError,
            NotWhitelistedError,
            FileTooLargeError,
            UnsupportedMimeError,
            ChannelNotFoundError,
            MessageNotFoundError,
            AskTimeoutError,
            AskOrphanedError,
            AskSendFailedError,
            SessionLockedError,
            KeyringUnavailableError,
            PollCursorLostError,
            PollAlreadyActiveError,
            ConfigInvalidError,
            RateLimitError,
            InternalError,
        ]:
            assert issubclass(cls, BridgeError)


class TestToErrorResponse:
    def test_basic_structure(self):
        exc = InternalError("something went wrong")
        resp = to_error_response(exc)
        assert "error" in resp
        assert resp["error"]["code"] == "INTERNAL"
        assert resp["error"]["message"] == "something went wrong"

    def test_flood_wait_includes_retry_after(self):
        exc = FloodWaitError("flood", retry_after_seconds=42)
        resp = to_error_response(exc)
        assert resp["error"]["retry_after_seconds"] == 42

    def test_session_locked_includes_holder_pid(self):
        exc = SessionLockedError("locked", holder_pid=12345)
        resp = to_error_response(exc)
        assert resp["error"]["holder_pid"] == 12345

    def test_session_locked_no_pid_no_field(self):
        exc = SessionLockedError("locked")
        resp = to_error_response(exc)
        assert "holder_pid" not in resp["error"]

    def test_not_whitelisted_batch_extras(self):
        exc = NotWhitelistedError("nope", already_downloaded_count=3, pending_count=7)
        resp = to_error_response(exc)
        assert resp["error"]["already_downloaded_count"] == 3
        assert resp["error"]["pending_count"] == 7

    def test_rate_limit_includes_retry(self):
        exc = RateLimitError("slow down", retry_after_seconds=5.0)
        resp = to_error_response(exc)
        assert resp["error"]["retry_after_seconds"] == 5.0

    def test_no_traceback_in_response(self):
        """Feed a real exception, verify no traceback in output."""
        try:
            raise InternalError("boom")
        except InternalError as exc:
            resp = to_error_response(exc)

        resp_str = str(resp)
        assert "Traceback" not in resp_str
        assert "File " not in resp_str
        assert "__traceback__" not in resp_str

    def test_response_only_has_error_key(self):
        exc = ConfigInvalidError("bad config")
        resp = to_error_response(exc)
        assert list(resp.keys()) == ["error"]

    def test_file_too_large_no_extra(self):
        exc = FileTooLargeError("too big")
        resp = to_error_response(exc)
        assert resp["error"]["code"] == "FILE_TOO_LARGE"
        assert set(resp["error"].keys()) == {"code", "message"}
