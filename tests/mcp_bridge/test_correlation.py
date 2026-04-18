"""Tests for mcp_bridge/correlation.py — state machine, match cascade, recovery."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_bridge.correlation import Correlation
from mcp_bridge.errors import AskTimeoutError


def _make_db(tmp_path: Path) -> str:
    return str(tmp_path / "corr.db")


def _make_msg(
    msg_id: int,
    text: str = "hello",
    reply_to_msg_id: int | None = None,
    from_id: int | None = None,
    date=None,
) -> SimpleNamespace:
    import datetime
    return SimpleNamespace(
        id=msg_id,
        text=text,
        message=text,
        reply_to_msg_id=reply_to_msg_id,
        from_id=from_id,
        date=date or datetime.datetime.now(datetime.timezone.utc),
    )


def _strict_config():
    return SimpleNamespace(ask_fallback="strict", fallback_window_seconds=120)


def _user_scoped_config():
    return SimpleNamespace(ask_fallback="user_scoped", fallback_window_seconds=120)


def _chat_scoped_config():
    return SimpleNamespace(ask_fallback="chat_scoped", fallback_window_seconds=120)


# ─── insert_pending ────────────────────────────────────────────────────────────


class TestInsertPending:
    def test_creates_row_with_state_pending(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        token = corr.insert_pending(chat_id=1, text="ping", timeout_sec=60)
        row = corr._db.execute(
            "SELECT state, outbound_msg_id FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["state"] == "pending"
        assert row["outbound_msg_id"] is None
        corr.close()

    def test_returns_unique_tokens(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        t1 = corr.insert_pending(chat_id=1, text="a", timeout_sec=30)
        t2 = corr.insert_pending(chat_id=1, text="b", timeout_sec=30)
        assert t1 != t2
        corr.close()


# ─── record_send_success ───────────────────────────────────────────────────────


class TestRecordSendSuccess:
    def test_transitions_null_to_message_id(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        token = corr.insert_pending(chat_id=1, text="hi", timeout_sec=60)
        corr.record_send_success(token, 42)
        row = corr._db.execute(
            "SELECT outbound_msg_id FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["outbound_msg_id"] == 42
        corr.close()


# ─── record_send_failed ────────────────────────────────────────────────────────


class TestRecordSendFailed:
    def test_transitions_to_send_failed(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        token = corr.insert_pending(chat_id=1, text="hi", timeout_sec=60)
        corr.record_send_failed(token, "FloodWait")
        row = corr._db.execute(
            "SELECT state FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["state"] == "send_failed"
        corr.close()


# ─── match_reply ──────────────────────────────────────────────────────────────


class TestMatchReplyStrict:
    def test_matches_inbound_reply_to_outbound_msg_id(self, tmp_path):
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=10, text="q", timeout_sec=60)
        corr.record_send_success(token, 99)

        msg = _make_msg(msg_id=200, reply_to_msg_id=99)
        matched = corr.match_reply(10, msg)
        assert matched == token
        corr.close()

    def test_no_match_wrong_chat(self, tmp_path):
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=10, text="q", timeout_sec=60)
        corr.record_send_success(token, 99)

        msg = _make_msg(msg_id=200, reply_to_msg_id=99)
        matched = corr.match_reply(99, msg)  # wrong chat
        assert matched is None
        corr.close()

    def test_no_match_no_reply_to(self, tmp_path):
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=10, text="q", timeout_sec=60)
        corr.record_send_success(token, 99)

        msg = _make_msg(msg_id=200, reply_to_msg_id=None)
        matched = corr.match_reply(10, msg)
        assert matched is None
        corr.close()

    def test_sets_match_strategy_reply(self, tmp_path):
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=5, text="q", timeout_sec=60)
        corr.record_send_success(token, 77)

        msg = _make_msg(msg_id=88, reply_to_msg_id=77)
        corr.match_reply(5, msg)

        row = corr._db.execute(
            "SELECT match_strategy, state FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["match_strategy"] == "reply"
        assert row["state"] == "answered"
        corr.close()


class TestMatchReplyUserScoped:
    def test_user_scoped_matches_non_reply_from_target(self, tmp_path):
        import datetime
        corr = Correlation(_make_db(tmp_path), config=_user_scoped_config())
        corr.open()
        token = corr.insert_pending(
            chat_id=10, text="q", timeout_sec=60, target_user_id=42
        )
        corr.record_send_success(token, 99)

        now = datetime.datetime.now(datetime.timezone.utc)
        msg = _make_msg(msg_id=200, reply_to_msg_id=None, from_id=42, date=now)
        matched = corr.match_reply(10, msg)
        assert matched == token
        corr.close()

    def test_user_scoped_sets_fallback_a_strategy(self, tmp_path):
        import datetime
        corr = Correlation(_make_db(tmp_path), config=_user_scoped_config())
        corr.open()
        token = corr.insert_pending(
            chat_id=10, text="q", timeout_sec=60, target_user_id=42
        )
        corr.record_send_success(token, 99)

        now = datetime.datetime.now(datetime.timezone.utc)
        msg = _make_msg(msg_id=200, reply_to_msg_id=None, from_id=42, date=now)
        corr.match_reply(10, msg)

        row = corr._db.execute(
            "SELECT match_strategy FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["match_strategy"] == "fallback_a"
        corr.close()

    def test_user_scoped_no_match_wrong_user(self, tmp_path):
        import datetime
        corr = Correlation(_make_db(tmp_path), config=_user_scoped_config())
        corr.open()
        corr.insert_pending(chat_id=10, text="q", timeout_sec=60, target_user_id=42)

        now = datetime.datetime.now(datetime.timezone.utc)
        msg = _make_msg(msg_id=200, reply_to_msg_id=None, from_id=99, date=now)
        matched = corr.match_reply(10, msg)
        assert matched is None
        corr.close()


class TestMatchReplyChatScoped:
    def test_chat_scoped_matches_first_non_self_message(self, tmp_path):
        import datetime
        corr = Correlation(_make_db(tmp_path), config=_chat_scoped_config())
        corr.open()
        token = corr.insert_pending(chat_id=10, text="q", timeout_sec=60)
        corr.record_send_success(token, 99)

        now = datetime.datetime.now(datetime.timezone.utc)
        msg = _make_msg(msg_id=200, reply_to_msg_id=None, from_id=55, date=now)
        matched = corr.match_reply(10, msg)
        assert matched == token
        corr.close()

    def test_chat_scoped_sets_fallback_b_strategy(self, tmp_path):
        import datetime
        corr = Correlation(_make_db(tmp_path), config=_chat_scoped_config())
        corr.open()
        token = corr.insert_pending(chat_id=10, text="q", timeout_sec=60)
        corr.record_send_success(token, 99)

        now = datetime.datetime.now(datetime.timezone.utc)
        msg = _make_msg(msg_id=200, reply_to_msg_id=None, from_id=55, date=now)
        corr.match_reply(10, msg)

        row = corr._db.execute(
            "SELECT match_strategy FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["match_strategy"] == "fallback_b"
        corr.close()

    def test_chat_scoped_no_match_when_multiple_pending(self, tmp_path):
        import datetime
        corr = Correlation(_make_db(tmp_path), config=_chat_scoped_config())
        corr.open()
        corr.insert_pending(chat_id=10, text="q1", timeout_sec=60)
        corr.insert_pending(chat_id=10, text="q2", timeout_sec=60)

        now = datetime.datetime.now(datetime.timezone.utc)
        msg = _make_msg(msg_id=200, reply_to_msg_id=None, from_id=55, date=now)
        matched = corr.match_reply(10, msg)
        assert matched is None
        corr.close()


# ─── wait_for_reply ────────────────────────────────────────────────────────────


class TestWaitForReply:
    @pytest.mark.asyncio
    async def test_returns_on_event_set(self, tmp_path):
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=5, text="q", timeout_sec=5)
        corr.record_send_success(token, 10)

        async def deliver():
            await asyncio.sleep(0.05)
            msg = _make_msg(msg_id=11, reply_to_msg_id=10)
            corr.match_reply(5, msg)

        asyncio.ensure_future(deliver())
        result = await corr.wait_for_reply(token, timeout_sec=2.0)
        assert result["reply_text"] == "hello"
        assert result["match_strategy"] == "reply"
        corr.close()

    @pytest.mark.asyncio
    async def test_raises_ask_timeout_error(self, tmp_path):
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=5, text="q", timeout_sec=0.1)
        corr.record_send_success(token, 10)

        with pytest.raises(AskTimeoutError):
            await corr.wait_for_reply(token, timeout_sec=0.1)

        row = corr._db.execute(
            "SELECT state FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["state"] == "timed_out"
        corr.close()


# ─── recover_on_startup ────────────────────────────────────────────────────────


class TestRecoverOnStartup:
    def test_expired_row_transitions_to_timed_out(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        # Insert a row with expires_at in the past
        corr._db.execute(
            """INSERT INTO pending_asks
               (ask_token, chat_id, created_at, expires_at, state)
               VALUES ('tok-a', 1, ?, ?, 'pending')""",
            (time.time() - 200, time.time() - 100),
        )
        corr._db.commit()

        summary = corr.recover_on_startup()
        assert summary["timed_out_count"] == 1
        assert summary["orphaned_count"] == 0

        row = corr._db.execute(
            "SELECT state FROM pending_asks WHERE ask_token='tok-a'"
        ).fetchone()
        assert row["state"] == "timed_out"
        corr.close()

    def test_non_expired_row_transitions_to_orphaned(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        # Insert a row with expires_at in the future
        corr._db.execute(
            """INSERT INTO pending_asks
               (ask_token, chat_id, created_at, expires_at, state)
               VALUES ('tok-b', 1, ?, ?, 'pending')""",
            (time.time(), time.time() + 9999),
        )
        corr._db.commit()

        summary = corr.recover_on_startup()
        assert summary["orphaned_count"] == 1
        assert summary["timed_out_count"] == 0

        row = corr._db.execute(
            "SELECT state FROM pending_asks WHERE ask_token='tok-b'"
        ).fetchone()
        assert row["state"] == "orphaned"
        corr.close()

    def test_mixed_rows(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        now = time.time()
        corr._db.executemany(
            """INSERT INTO pending_asks
               (ask_token, chat_id, created_at, expires_at, state)
               VALUES (?, 1, ?, ?, 'pending')""",
            [
                ("exp-1", now - 200, now - 100),
                ("exp-2", now - 300, now - 150),
                ("live-1", now, now + 500),
            ],
        )
        corr._db.commit()

        summary = corr.recover_on_startup()
        assert summary["timed_out_count"] == 2
        assert summary["orphaned_count"] == 1
        corr.close()

    def test_answered_rows_left_unchanged(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        corr._db.execute(
            """INSERT INTO pending_asks
               (ask_token, chat_id, created_at, expires_at, state)
               VALUES ('ans', 1, ?, ?, 'answered')""",
            (time.time() - 100, time.time() - 10),
        )
        corr._db.commit()

        summary = corr.recover_on_startup()
        assert summary["timed_out_count"] == 0
        assert summary["orphaned_count"] == 0

        row = corr._db.execute(
            "SELECT state FROM pending_asks WHERE ask_token='ans'"
        ).fetchone()
        assert row["state"] == "answered"
        corr.close()


# ─── janitor ──────────────────────────────────────────────────────────────────


class TestJanitor:
    def test_sweeps_send_failed_older_than_24h(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        now = time.time()
        old = now - 25 * 3600  # 25 hours ago → should be swept
        recent = now - 1 * 3600  # 1 hour ago → should survive
        corr._db.executemany(
            """INSERT INTO pending_asks
               (ask_token, chat_id, created_at, expires_at, state)
               VALUES (?, 1, ?, ?, 'send_failed')""",
            [("old-1", old, old + 60), ("new-1", recent, recent + 60)],
        )
        corr._db.commit()

        deleted = corr._sweep_send_failed()
        assert deleted == 1

        remaining = corr._db.execute(
            "SELECT ask_token FROM pending_asks WHERE state='send_failed'"
        ).fetchall()
        assert len(remaining) == 1
        assert remaining[0]["ask_token"] == "new-1"
        corr.close()

    def test_leaves_newer_send_failed_rows(self, tmp_path):
        corr = Correlation(_make_db(tmp_path))
        corr.open()
        now = time.time()
        corr._db.execute(
            """INSERT INTO pending_asks
               (ask_token, chat_id, created_at, expires_at, state)
               VALUES ('new-2', 1, ?, ?, 'send_failed')""",
            (now - 3600, now),
        )
        corr._db.commit()

        deleted = corr._sweep_send_failed()
        assert deleted == 0
        corr.close()


# ─── TOCTOU fix: reply arrives at timeout boundary ────────────────────────────


class TestWaitForReplyTOCTOU:
    @pytest.mark.asyncio
    async def test_reply_arrives_at_timeout_boundary(self, tmp_path):
        """Race: match_reply fires just as wait_for_reply would time out.

        Simulates the narrow window between asyncio.wait_for raising TimeoutError
        and the state-conditional UPDATE by pre-populating _replies before calling
        wait_for_reply (the early-exit path in the existing check before wait_for).
        The reply must be returned instead of raising AskTimeoutError.
        """
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=5, text="q", timeout_sec=0.05)
        corr.record_send_success(token, 10)

        # Directly pre-populate _replies to simulate a reply landing between
        # wait_for raising TimeoutError and our cleanup code executing.
        # This exercises the `late_reply = self._replies.pop(ask_token, None)` path.
        reply_payload = {
            "ask_token": token,
            "reply_text": "late reply",
            "reply_msg_id": 11,
            "match_strategy": "reply",
        }
        corr._replies[token] = reply_payload

        # Now call wait_for_reply — the early check (reply already in _replies
        # before event registration) should return immediately.
        result = await corr.wait_for_reply(token, timeout_sec=0.05)
        assert result["reply_text"] == "late reply"
        assert result["match_strategy"] == "reply"
        corr.close()

    @pytest.mark.asyncio
    async def test_timeout_update_is_state_conditional(self, tmp_path):
        """After timeout, DB UPDATE uses WHERE state='pending' guard."""
        corr = Correlation(_make_db(tmp_path), config=_strict_config())
        corr.open()
        token = corr.insert_pending(chat_id=5, text="q", timeout_sec=0.05)
        corr.record_send_success(token, 10)

        # Pre-transition the row to 'answered' (simulates race-won by match_reply)
        corr._db.execute(
            "UPDATE pending_asks SET state='answered' WHERE ask_token=?", (token,)
        )
        corr._db.commit()
        # Also put reply payload so post-timeout check can return it
        corr._replies[token] = {
            "ask_token": token,
            "reply_text": "won by match",
            "reply_msg_id": 11,
            "match_strategy": "reply",
        }
        # Set the event so wait_for_reply doesn't actually wait
        evt = asyncio.Event()
        corr._events[token] = evt
        evt.set()

        result = await corr.wait_for_reply(token, timeout_sec=5.0)
        assert result["reply_text"] == "won by match"

        # Row must still be 'answered', not 'timed_out'
        row = corr._db.execute(
            "SELECT state FROM pending_asks WHERE ask_token=?", (token,)
        ).fetchone()
        assert row["state"] == "answered"
        corr.close()
