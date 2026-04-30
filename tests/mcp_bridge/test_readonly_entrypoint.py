"""Smoke tests for the read-only entrypoint."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


def test_module_sets_env_var_on_import():
    """Importing the module must set MCP_READONLY=1 even before main() runs."""
    code = (
        "import os, sys\n"
        "os.environ.pop('MCP_READONLY', None)\n"
        "import mcp_bridge.server_readonly  # noqa: F401\n"
        "print(os.environ.get('MCP_READONLY'))\n"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
    assert out == "1"


def test_help_exits_zero():
    """`python -m mcp_bridge.server_readonly --help` exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_bridge.server_readonly", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert "Telethon MCP Bridge" in proc.stdout
