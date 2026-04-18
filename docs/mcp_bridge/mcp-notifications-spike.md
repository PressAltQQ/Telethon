# MCP Notifications Spike — Claude Code Server-Push Support

**Date:** 2026-04-18
**Question:** Does Claude Code's MCP stdio transport reliably surface server-initiated notifications to the agent within a Claude Code session?
**Verdict:** **NO-GO**

## Evidence

- Claude Code's documented MCP integration focuses on the request/response tool-call model. While the MCP protocol itself defines server-initiated `notifications/*` messages, Claude Code's client did not, at the time of this spike, reliably surface those unsolicited notifications into the live agent transcript.
- Community reports on the `claude-code` GitHub issue tracker describe server-side `notifications/message` / custom notifications being emitted but not delivered to the session. Behavior has been inconsistent across versions.
- Anthropic's own MCP docs mention the capability aspirationally ("an MCP server can push messages into your session") but do not guarantee it for the stdio transport in the current CLI.

Because the realtime-Q&A JTBD requires < 2 s p95 end-to-end latency AND the design commitment from §2.6 is that a failed push path blocks all subsequent sprints, we fail-closed here.

## Decision

Adopt polling as the primary realtime delivery mechanism. Remove `subscribe_chat` from v1 scope. Replace with `poll_chat_since(chat_id, since_message_id, timeout_ms?)` — a long-polling tool call that returns new messages as soon as they arrive, or after a short server-side wait.

## Implications

- Spec §2.6 is revised to make polling the primary API. Subscribe/notification mode is deferred to a future v2 once Claude Code push support is confirmed.
- Plan Sprint 5 is revised: implement `poll_chat_since` (and a trivial `get_new_messages_since`) instead of `subscribe_chat` / `unsubscribe`.
- SC3 (< 2 s p95) remains achievable: long-polling with a 1.5 s server-side wait window delivers messages almost immediately on arrival; worst-case = the configured poll interval.

## Recommended parameters

- Default long-poll server-side wait: **1500 ms** (keeps p95 < 2 s even with a 300 ms agent decision latency).
- Client poll cadence: immediate re-poll after each response (simple loop), no fixed client-side sleep.
