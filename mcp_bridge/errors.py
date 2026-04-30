"""
Typed bridge exceptions with structured error response helpers.

Each exception class has a CODE class attribute matching spec §5.
The to_error_response() helper returns {"error": {"code": ..., "message": ..., ...extra}}
and NEVER includes tracebacks.
"""
from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    """Base class for all mcp_bridge errors."""

    CODE: str = "INTERNAL"

    def extra_fields(self) -> dict[str, Any]:
        """Override to add extra fields to the error response."""
        return {}


class FloodWaitError(BridgeError):
    CODE = "FLOOD_WAIT"

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

    def extra_fields(self) -> dict[str, Any]:
        return {"retry_after_seconds": self.retry_after_seconds}


class NotWhitelistedError(BridgeError):
    CODE = "NOT_WHITELISTED"

    def __init__(
        self,
        message: str,
        already_downloaded_count: int | None = None,
        pending_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.already_downloaded_count = already_downloaded_count
        self.pending_count = pending_count

    def extra_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if self.already_downloaded_count is not None:
            fields["already_downloaded_count"] = self.already_downloaded_count
        if self.pending_count is not None:
            fields["pending_count"] = self.pending_count
        return fields


class FileTooLargeError(BridgeError):
    CODE = "FILE_TOO_LARGE"


class UnsupportedMimeError(BridgeError):
    CODE = "UNSUPPORTED_MIME"


class ChannelNotFoundError(BridgeError):
    CODE = "CHANNEL_NOT_FOUND"


class MessageNotFoundError(BridgeError):
    CODE = "MESSAGE_NOT_FOUND"


class AskTimeoutError(BridgeError):
    CODE = "ASK_TIMEOUT"


class AskOrphanedError(BridgeError):
    CODE = "ASK_ORPHANED"


class AskSendFailedError(BridgeError):
    CODE = "ASK_SEND_FAILED"


class SessionLockedError(BridgeError):
    CODE = "SESSION_LOCKED"

    def __init__(self, message: str, holder_pid: int | None = None) -> None:
        super().__init__(message)
        self.holder_pid = holder_pid

    def extra_fields(self) -> dict[str, Any]:
        if self.holder_pid is not None:
            return {"holder_pid": self.holder_pid}
        return {}


class KeyringUnavailableError(BridgeError):
    CODE = "KEYRING_UNAVAILABLE"


class PollCursorLostError(BridgeError):
    CODE = "POLL_CURSOR_LOST"


class PollAlreadyActiveError(BridgeError):
    CODE = "POLL_ALREADY_ACTIVE"


class ConfigInvalidError(BridgeError):
    CODE = "CONFIG_INVALID"


class RateLimitError(BridgeError):
    CODE = "RATE_LIMIT"

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

    def extra_fields(self) -> dict[str, Any]:
        if self.retry_after_seconds is not None:
            return {"retry_after_seconds": self.retry_after_seconds}
        return {}


class InternalError(BridgeError):
    CODE = "INTERNAL"


class NotImplementedBridgeError(BridgeError):
    CODE = "NOT_IMPLEMENTED"


class DownloadBusyError(BridgeError):
    CODE = "DOWNLOAD_BUSY"


class ReadOnlyBlockedError(BridgeError):
    CODE = "READONLY_BLOCKED"


def to_error_response(exc: BridgeError) -> dict[str, Any]:
    """Convert a BridgeError to a structured MCP error dict.

    Never includes tracebacks or raw exception internals.
    """
    error: dict[str, Any] = {
        "code": exc.CODE,
        "message": str(exc),
    }
    error.update(exc.extra_fields())
    return {"error": error}
