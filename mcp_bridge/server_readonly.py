"""
Read-only entrypoint for the MCP bridge.

Sets ``MCP_READONLY=1`` before delegating to the normal main(). Provides a
misuse-resistant launch path: a systemd/launchd unit that invokes
``python -m mcp_bridge.server_readonly`` cannot accidentally start the full
read/write server even if the env var is missing from the unit file.
"""
from __future__ import annotations

import os

os.environ.setdefault("MCP_READONLY", "1")

from mcp_bridge.__main__ import main

if __name__ == "__main__":
    main()
