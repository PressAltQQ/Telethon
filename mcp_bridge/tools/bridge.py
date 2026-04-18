"""
Q&A bridge tools: send_message and ask_user.

send_message:
  - whitelist gate (write_chats)
  - rate-limiter acquire
  - Telethon client.send_message → capture message_id
  - translate Telethon FloodWaitError to bridge FloodWaitError

ask_user:
  - whitelist gate (ask_chats)
  - rate-limiter acquire (ONE token at send time, not during wait)
  - insert_pending → send_message → record_send_success / record_send_failed
  - wait_for_reply (asyncio.Event, timeout)
"""
from __future__ import annotations

import logging
from typing import Any

from mcp_bridge.errors import (
    AskSendFailedError,
    NotWhitelistedError,
)
from mcp_bridge.errors import (
    FloodWaitError as BridgeFloodWaitError,
)
from mcp_bridge.filters import is_whitelisted
from mcp_bridge.rate_limit import get_rate_limiter

__log__ = logging.getLogger(__name__)


def _translate_telethon_error(exc: Exception) -> Exception:
    """Translate a raw Telethon exception to a bridge-typed error.

    Returns the original exception if no translation applies.
    """
    # Import lazily to avoid hard dependency on Telethon error types
    exc_type_name = type(exc).__name__

    if exc_type_name == "FloodWaitError":
        seconds = getattr(exc, "seconds", 0)
        return BridgeFloodWaitError(
            f"Telegram FloodWait: retry after {seconds}s",
            retry_after_seconds=int(seconds),
        )

    # Check for base Telethon RPC / chat errors
    if exc_type_name in ("ChatIdInvalidError", "PeerIdInvalidError"):
        from mcp_bridge.errors import ChannelNotFoundError
        return ChannelNotFoundError(str(exc))

    return exc


async def send_message(client, config, chat_id: int, text: str) -> dict[str, Any]:
    """Send a message to a whitelisted chat.

    Returns {"message_id": int, "date": str}.
    Raises NotWhitelistedError, BridgeFloodWaitError, or re-raises translated errors.
    """
    if not is_whitelisted(chat_id, "write_chats", config):
        raise NotWhitelistedError(f"chat_id={chat_id} is not in write_chats whitelist")

    rate_limiter = get_rate_limiter(config)
    await rate_limiter.acquire()

    try:
        message = await client.send_message(chat_id, text)
    except Exception as exc:
        translated = _translate_telethon_error(exc)
        if translated is exc:
            raise
        else:
            raise translated from exc

    msg_id = getattr(message, "id", None)
    msg_date = getattr(message, "date", None)

    return {
        "message_id": msg_id,
        "date": str(msg_date) if msg_date is not None else None,
    }


async def ask_user(
    client,
    correlation,
    config,
    chat_id: int,
    text: str,
    timeout_sec: float = 1800,
    target_user_id: int | None = None,
) -> dict[str, Any]:
    """Send a message and wait for a reply.

    Returns {"ask_token": str, "reply_text": str, "match_strategy": str}.
    Raises NotWhitelistedError, RateLimitError, AskSendFailedError, AskTimeoutError.
    """
    if not is_whitelisted(chat_id, "ask_chats", config):
        raise NotWhitelistedError(f"chat_id={chat_id} is not in ask_chats whitelist")

    # Consume ONE token at send time
    rate_limiter = get_rate_limiter(config)
    await rate_limiter.acquire()

    # Send FIRST so we have message_id before inserting the pending row.
    # If send fails, no orphaned pending row is created in the DB.
    try:
        message = await client.send_message(chat_id, text)
    except Exception as exc:
        translated = _translate_telethon_error(exc)
        translated_code = getattr(translated, "CODE", type(translated).__name__)
        __log__.warning(
            "ask_user send_message failed for chat_id=%s: %s",
            chat_id, exc,
        )
        raise AskSendFailedError(
            f"send_message failed for chat_id={chat_id}: {translated_code}",
        ) from exc

    msg_id = getattr(message, "id", None)

    ask_token = correlation.insert_pending(
        chat_id=chat_id,
        text=text,
        timeout_sec=timeout_sec,
        target_user_id=target_user_id,
    )

    if msg_id is not None:
        correlation.record_send_success(ask_token, msg_id)

    reply = await correlation.wait_for_reply(ask_token, timeout_sec)
    return {
        "ask_token": ask_token,
        "reply_text": reply.get("reply_text", ""),
        "match_strategy": reply.get("match_strategy", ""),
    }
