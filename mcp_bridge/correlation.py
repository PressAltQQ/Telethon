"""
Correlation state machine for the Q&A bridge.

SQLite table ``pending_asks`` tracks outbound messages and their replies.
Startup recovery handles crashed/orphaned rows. A janitor sweeps old
send_failed rows.

State transitions:
  pending → answered     (match_reply succeeds)
  pending → timed_out    (wait_for_reply times out OR expired at startup)
  pending → send_failed  (record_send_failed)
  pending → orphaned     (non-expired at startup recovery)
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from typing import Any

from mcp_bridge.errors import AskTimeoutError

__log__ = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS pending_asks (
    ask_token       TEXT PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    outbound_msg_id INTEGER,
    target_user_id  INTEGER,
    created_at      REAL,
    expires_at      REAL,
    reply_msg_id    INTEGER,
    reply_text      TEXT,
    state           TEXT,
    match_strategy  TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_asks_chat_outbound
    ON pending_asks (chat_id, outbound_msg_id);
"""

_JANITOR_INTERVAL_SECONDS = 60
_SEND_FAILED_TTL_SECONDS = 24 * 3600  # 24 h


class Correlation:
    """Owns the pending_asks SQLite DB and the in-memory event map."""

    def __init__(self, db_path: str, config=None) -> None:
        self._db_path = db_path
        self._config = config
        self._conn: sqlite3.Connection | None = None
        # ask_token → asyncio.Event
        self._events: dict[str, asyncio.Event] = {}
        # ask_token → reply payload dict (set before event.set())
        self._replies: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open (or create) the SQLite connection and apply the schema.

        Idempotent: if the connection is already open, this is a no-op.
        """
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Correlation.open() must be called before use")
        return self._conn

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def insert_pending(
        self,
        chat_id: int,
        text: str,
        timeout_sec: float,
        target_user_id: int | None = None,
    ) -> str:
        """Insert a new pending ask row, return the ask_token UUID."""
        ask_token = str(uuid.uuid4())
        now = time.time()
        self._db.execute(
            """
            INSERT INTO pending_asks
                (ask_token, chat_id, outbound_msg_id, target_user_id,
                 created_at, expires_at, state)
            VALUES (?, ?, NULL, ?, ?, ?, 'pending')
            """,
            (ask_token, chat_id, target_user_id, now, now + timeout_sec),
        )
        self._db.commit()
        return ask_token

    def record_send_success(self, ask_token: str, outbound_msg_id: int) -> None:
        """Update the pending row with the Telegram message ID."""
        self._db.execute(
            "UPDATE pending_asks SET outbound_msg_id = ? WHERE ask_token = ?",
            (outbound_msg_id, ask_token),
        )
        self._db.commit()

    def record_send_failed(self, ask_token: str, reason: str) -> None:
        """Transition the row to send_failed."""
        self._db.execute(
            "UPDATE pending_asks SET state = 'send_failed' WHERE ask_token = ?",
            (ask_token,),
        )
        self._db.commit()
        __log__.warning("ask_token=%s send_failed: %s", ask_token, reason)

    def match_reply(self, chat_id: int, msg) -> str | None:
        """Try to match an inbound message to a pending ask.

        ``msg`` must have attributes:
          - ``id``            (int) message ID
          - ``text`` or ``message`` (str)
          - ``reply_to_msg_id`` (int | None)
          - ``from_id``       (int | None) sender user ID
          - ``date``          (datetime | None)

        Returns the matched ask_token or None.
        Transitions the row to 'answered' and sets the asyncio.Event.
        """
        # Gather message attributes safely
        msg_id: int = getattr(msg, "id", 0)
        reply_to: int | None = getattr(msg, "reply_to_msg_id", None)
        sender_id: int | None = getattr(msg, "from_id", None)
        text: str = getattr(msg, "text", None) or getattr(msg, "message", "") or ""

        now = time.time()

        # --- Primary: reply_to_msg_id match ---
        if reply_to is not None:
            row = self._db.execute(
                """
                SELECT ask_token, target_user_id
                FROM pending_asks
                WHERE chat_id = ? AND outbound_msg_id = ? AND state = 'pending'
                  AND expires_at > ?
                LIMIT 1
                """,
                (chat_id, reply_to, now),
            ).fetchone()
            if row:
                token = row["ask_token"]
                self._set_answered(token, msg_id, text, "reply")
                return token

        # Determine fallback mode from config
        ask_fallback = "strict"
        fallback_window = 120
        if self._config is not None:
            ask_fallback = getattr(self._config, "ask_fallback", "strict")
            fallback_window = getattr(self._config, "fallback_window_seconds", 120)

        if ask_fallback == "strict":
            return None

        window_start = now - fallback_window

        # --- Fallback A: user_scoped ---
        if ask_fallback in ("user_scoped", "chat_scoped") and sender_id is not None:
            rows = self._db.execute(
                """
                SELECT ask_token, target_user_id
                FROM pending_asks
                WHERE chat_id = ? AND state = 'pending' AND expires_at > ?
                  AND target_user_id IS NOT NULL
                """,
                (chat_id, now),
            ).fetchall()
            # Exactly one pending row for this (chat, target_user_id)
            matching = [r for r in rows if r["target_user_id"] == sender_id]
            if len(matching) == 1 and reply_to is None:
                # Message must be within the fallback window
                msg_date = getattr(msg, "date", None)
                if msg_date is not None:
                    if hasattr(msg_date, "timestamp"):
                        ts = msg_date.timestamp()
                    else:
                        ts = float(msg_date)
                    if ts >= window_start:
                        token = matching[0]["ask_token"]
                        self._set_answered(token, msg_id, text, "fallback_a")
                        return token

        # --- Fallback B: chat_scoped ---
        if ask_fallback == "chat_scoped":
            rows = self._db.execute(
                """
                SELECT ask_token
                FROM pending_asks
                WHERE chat_id = ? AND state = 'pending' AND expires_at > ?
                """,
                (chat_id, now),
            ).fetchall()
            if len(rows) == 1 and reply_to is None:
                msg_date = getattr(msg, "date", None)
                if msg_date is not None:
                    if hasattr(msg_date, "timestamp"):
                        ts = msg_date.timestamp()
                    else:
                        ts = float(msg_date)
                    if ts >= window_start:
                        token = rows[0]["ask_token"]
                        self._set_answered(token, msg_id, text, "fallback_b")
                        return token

        return None

    def _set_answered(
        self, ask_token: str, reply_msg_id: int, reply_text: str, match_strategy: str
    ) -> None:
        """Transition to answered and trigger the in-memory event."""
        self._db.execute(
            """
            UPDATE pending_asks
            SET state = 'answered',
                reply_msg_id = ?,
                reply_text = ?,
                match_strategy = ?
            WHERE ask_token = ?
            """,
            (reply_msg_id, reply_text, match_strategy, ask_token),
        )
        self._db.commit()
        payload = {
            "ask_token": ask_token,
            "reply_text": reply_text,
            "reply_msg_id": reply_msg_id,
            "match_strategy": match_strategy,
        }
        self._replies[ask_token] = payload
        event = self._events.get(ask_token)
        if event is not None:
            event.set()

    async def wait_for_reply(self, ask_token: str, timeout_sec: float) -> dict[str, Any]:
        """Wait for a reply event. Raises AskTimeoutError on timeout."""
        event = asyncio.Event()
        self._events[ask_token] = event

        # Check if already answered (race: reply arrived before we registered the event)
        if ask_token in self._replies:
            self._events.pop(ask_token, None)
            return self._replies.pop(ask_token)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            self._events.pop(ask_token, None)
            # Race guard: check if a reply landed in the window between
            # wait_for raising TimeoutError and our cleanup.
            # NOTE: match_reply MUST run on the same asyncio loop thread.
            late_reply = self._replies.pop(ask_token, None)
            if late_reply is not None:
                # Reply won the race — return it instead of timing out.
                # State-conditional UPDATE: do NOT overwrite 'answered'.
                self._db.execute(
                    "UPDATE pending_asks SET state = 'timed_out' "
                    "WHERE ask_token = ? AND state = 'pending'",
                    (ask_token,),
                )
                self._db.commit()
                return late_reply
            # State-conditional UPDATE: only move to timed_out if still pending.
            cur = self._db.execute(
                "UPDATE pending_asks SET state = 'timed_out' "
                "WHERE ask_token = ? AND state = 'pending'",
                (ask_token,),
            )
            self._db.commit()
            if cur.rowcount == 0:
                # Zero rows affected → race was won by match_reply; re-read the reply.
                row = self._db.execute(
                    "SELECT reply_text, reply_msg_id, match_strategy "
                    "FROM pending_asks WHERE ask_token = ?",
                    (ask_token,),
                ).fetchone()
                if row is not None:
                    return {
                        "ask_token": ask_token,
                        "reply_text": row["reply_text"] or "",
                        "reply_msg_id": row["reply_msg_id"],
                        "match_strategy": row["match_strategy"] or "",
                    }
            raise AskTimeoutError(f"No reply within {timeout_sec}s for ask_token={ask_token}")

        self._events.pop(ask_token, None)
        payload = self._replies.pop(ask_token)
        return payload

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def recover_on_startup(self) -> dict[str, int]:
        """Handle pending rows from before a daemon restart.

        Expired rows → timed_out (WARNING log).
        Non-expired rows → orphaned (WARNING log, NOT re-armed).

        Returns {"timed_out_count": N, "orphaned_count": M}.
        """
        now = time.time()
        timed_out_count = 0
        orphaned_count = 0

        rows = self._db.execute(
            "SELECT ask_token, chat_id, outbound_msg_id, expires_at "
            "FROM pending_asks WHERE state = 'pending'"
        ).fetchall()

        for row in rows:
            token = row["ask_token"]
            if row["expires_at"] <= now:
                self._db.execute(
                    "UPDATE pending_asks SET state = 'timed_out' WHERE ask_token = ?",
                    (token,),
                )
                __log__.warning(
                    "startup recovery: ask_token=%s chat_id=%s outbound_msg_id=%s → timed_out",
                    token, row["chat_id"], row["outbound_msg_id"],
                )
                timed_out_count += 1
            else:
                self._db.execute(
                    "UPDATE pending_asks SET state = 'orphaned' WHERE ask_token = ?",
                    (token,),
                )
                __log__.warning(
                    "startup recovery: ask_token=%s chat_id=%s outbound_msg_id=%s → orphaned "
                    "(expires_at=%.0f, do NOT re-arm)",
                    token, row["chat_id"], row["outbound_msg_id"], row["expires_at"],
                )
                orphaned_count += 1

        self._db.commit()
        return {"timed_out_count": timed_out_count, "orphaned_count": orphaned_count}

    # ------------------------------------------------------------------
    # Janitor
    # ------------------------------------------------------------------

    async def janitor_loop(self) -> None:
        """Every 60 s, delete send_failed rows older than 24 h."""
        while True:
            try:
                await asyncio.sleep(_JANITOR_INTERVAL_SECONDS)
                self._sweep_send_failed()
            except asyncio.CancelledError:
                break
            except Exception:
                __log__.exception("janitor_loop error")

    def _sweep_send_failed(self) -> int:
        """Delete send_failed rows older than 24 h. Returns deleted count."""
        cutoff = time.time() - _SEND_FAILED_TTL_SECONDS
        cur = self._db.execute(
            "DELETE FROM pending_asks WHERE state = 'send_failed' AND created_at < ?",
            (cutoff,),
        )
        self._db.commit()
        deleted = cur.rowcount
        if deleted:
            __log__.info("janitor: swept %d send_failed rows", deleted)
        return deleted

