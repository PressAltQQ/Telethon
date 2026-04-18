PressAltQQ/Telethon — Private Security-Hardened Fork
=====================================================

This is a **private, personal fork** of Telethon maintained by PressAltQQ.
It diverges from upstream only by security posture — no intentional feature
divergence. The fork exists to harden MTProto handling and add a personal
MCP bridge for local automation.

Upstream project
----------------

Original Telethon by Lonami Exo: https://codeberg.org/Lonami/Telethon

All credit for the core library goes to the upstream maintainers. This fork
is not affiliated with or endorsed by the upstream project.

Install (private use)
---------------------

.. code-block:: sh

    uv sync --frozen

Requires Python 3.11+. See ``docs/mcp_bridge/README.md`` for the full
install runbook including first-run session setup.

MCP Bridge
----------

The ``mcp_bridge/`` package exposes Telethon via MCP stdio for personal
automation use. It is not part of the public Telethon API surface.

Tools provided:

- ``list_channel_files`` — list documents in whitelisted channels
- ``download_file`` — download a file by channel + message ID (idempotent)
- ``send_message`` — send text to a whitelisted chat
- ``ask_user`` — blocking Q&A with reply correlation
- ``poll_chat_since`` — long-poll for new messages (realtime, < 2 s p95)

See ``docs/mcp_bridge/README.md`` for the full install runbook (SC6: < 15 min
setup on a fresh machine) and configuration reference.

Note: ``telethon.sync`` is not supported in this fork and may be removed in
a future sprint.
