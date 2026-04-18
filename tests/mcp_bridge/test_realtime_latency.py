"""
SC3 latency test scaffold — Sprint 4.

This file contains the scaffolding for the realtime latency test.
Sprint 5 will:
  - Remove the skip marker
  - Increase N to 200
  - Add the ``assert p95 < 2000`` assertion
  - Wire the full update-handler long-poll loop

The scaffold MUST import cleanly and the marked test must be skipped
(not errored) when collected by pytest.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from mcp_bridge.correlation import Correlation


@pytest.mark.slow
@pytest.mark.skip(reason="Sprint 5 completes the SC3 assertion and increases N to 200")
async def test_sc3_roundtrip_latency_p95(tmp_path):
    """Measure p50/p95/p99 roundtrip through match_reply → wait_for_reply.

    N=10 messages with random jitter. Sprint 5 raises N to 200 and adds
    the ``assert p95 < 2000`` gate.

    Measurement: t_inject_ns → t_notify_ns (bridge internal cost only;
    no network / Telegram latency).
    """
    import datetime
    import random

    N = 10
    db_path = str(tmp_path / "latency_corr.db")
    corr = Correlation(db_path)
    corr.open()

    config = SimpleNamespace(ask_fallback="strict", fallback_window_seconds=120)
    corr._config = config

    latencies_ms: list[float] = []

    async def inject_message(token: str, outbound_msg_id: int, chat_id: int) -> None:
        jitter_ms = random.uniform(1, 50)  # reduced jitter for scaffold
        await asyncio.sleep(jitter_ms / 1000)

        t_inject_ns = time.monotonic_ns()

        msg = SimpleNamespace(
            id=outbound_msg_id + 1000,
            text="reply",
            message="reply",
            reply_to_msg_id=outbound_msg_id,
            from_id=999,
            date=datetime.datetime.now(datetime.timezone.utc),
            _t_inject_ns=t_inject_ns,
        )
        corr.match_reply(chat_id, msg)

    tokens = []
    for i in range(N):
        chat_id = 1000 + i
        token = corr.insert_pending(
            chat_id=chat_id, text=f"q{i}", timeout_sec=10.0
        )
        corr.record_send_success(token, i + 1)
        tokens.append((token, i + 1, chat_id))

    async def measure_one(token: str, outbound_id: int, chat_id: int) -> float:
        t_start = time.monotonic_ns()
        inject_task = asyncio.ensure_future(inject_message(token, outbound_id, chat_id))
        await corr.wait_for_reply(token, timeout_sec=5.0)
        t_end = time.monotonic_ns()
        await inject_task
        return (t_end - t_start) / 1e6  # ns → ms

    results = await asyncio.gather(
        *[measure_one(t, mid, cid) for t, mid, cid in tokens]
    )
    latencies_ms = list(results)

    def percentile(data: list[float], pct: float) -> float:
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * pct / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]

    p50 = percentile(latencies_ms, 50)
    p95 = percentile(latencies_ms, 95)
    p99 = percentile(latencies_ms, 99)

    print(f"\nSC3 latency scaffold (N={N}): p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")

    # Sprint 5 adds: assert p95 < 2000, "p95 latency exceeds 2000 ms SC3 contract"

    corr.close()
