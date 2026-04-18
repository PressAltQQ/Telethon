"""
SC3 latency test — Sprint 5 (complete).

Measures p50/p95/p99 roundtrip latency through poll_chat_since
using poll.ingest_message as the injection point.

N=200 messages, each trial starts a poll task BEFORE injecting so the
measurement covers the real asyncio Condition wakeup path, not the fast-path
short-circuit (message already buffered).

Assertion: p95 < 2000 ms.
Histogram printed on failure.

The test is marked @pytest.mark.slow — runs in the slow lane.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from mcp_bridge.tools import poll


@pytest.fixture(autouse=True)
def reset_poll_state():
    poll.reset_state()
    poll.set_buffer_size(512)
    yield
    poll.reset_state()
    poll.set_buffer_size(256)


def make_config():
    return SimpleNamespace(
        read_chats=[5001],
        poll_buffer_size=512,
    )


def make_metadata(msg_id: int) -> dict:
    return {
        "message_id": msg_id,
        "from_id": 1,
        "text": f"msg-{msg_id}",
        "reply_to_msg_id": None,
        "date": "2026-01-01T00:00:00",
        "has_media": False,
        "media_summary": None,
    }


@pytest.mark.slow
@pytest.mark.asyncio
async def test_sc3_roundtrip_latency_p95():
    """Measure p50/p95/p99 via real Condition wakeup path.

    Each trial:
    1. Start poll_chat_since task (no messages buffered yet — it will wait).
    2. Yield with asyncio.sleep(0) so the poll task reaches cond.wait_for().
    3. Inject the message — this notifies the condition.
    4. Measure time from inject to task completion.

    N=200. Assertion: p95 < 2000 ms.
    """
    N = 200
    config = make_config()
    chat_id = 5001

    latencies_ms: list[float] = []

    async def one_trial(msg_id: int, conn_id: str) -> float:
        """Start poll BEFORE inject to exercise the cond.wait_for wakeup."""
        # Start poll waiting for a message newer than msg_id - 1
        poll_task = asyncio.create_task(
            poll.poll_chat_since(
                config=config,
                chat_id=chat_id,
                since_message_id=msg_id - 1,
                timeout_ms=5000,
                connection_id=conn_id,
            )
        )
        # Yield so poll_task reaches cond.wait_for() before we inject
        await asyncio.sleep(0)

        t_inject = time.monotonic_ns()
        poll.ingest_message(chat_id, make_metadata(msg_id))
        result = await poll_task
        t_notify = time.monotonic_ns()

        _ = result  # serialization point — latency measured up to here
        return (t_notify - t_inject) / 1_000_000  # ns → ms

    for i in range(1, N + 1):
        lat = await one_trial(i, conn_id=f"latency-{i}")
        latencies_ms.append(lat)

    def percentile(data: list[float], pct: float) -> float:
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * pct / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]

    p50 = percentile(latencies_ms, 50)
    p95 = percentile(latencies_ms, 95)
    p99 = percentile(latencies_ms, 99)

    def build_histogram(data: list[float], buckets=10) -> str:
        min_val = min(data)
        max_val = max(data)
        if min_val == max_val:
            return f"[{min_val:.1f}ms: {len(data)}]"
        bucket_size = (max_val - min_val) / buckets
        hist: dict[str, int] = {}
        for v in data:
            bucket_idx = min(int((v - min_val) / bucket_size), buckets - 1)
            lo = min_val + bucket_idx * bucket_size
            hi = min_val + (bucket_idx + 1) * bucket_size
            bucket_label = f"{lo:.0f}-{hi:.0f}ms"
            hist[bucket_label] = hist.get(bucket_label, 0) + 1
        return str(hist)

    hist = build_histogram(latencies_ms)
    print(f"\nSC3 latency (N={N}): p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")
    print(f"Histogram: {hist}")

    assert p95 < 2000, f"p95 {p95:.1f} ms exceeds 2000 ms SC3 contract. histogram: {hist}"
