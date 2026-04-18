"""
Entry point for python -m mcp_bridge.

CLI flags:
  --config PATH          Path to TOML config file
  --first-run            Initialize keyring and session on first run
  --migrate-to-encrypted Migrate existing plaintext session to encrypted
  --allow-plaintext      Allow plaintext session (one-shot migration runs only)

Exit codes:
  0 — ok
  1 — config error
  2 — session error
  3 — runtime error
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

__log__ = logging.getLogger(__name__)


def _parse_args():
    parser = argparse.ArgumentParser(
        prog="python -m mcp_bridge",
        description="Telethon MCP Bridge — stdio MCP server",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to TOML config file (default: $TELETHON_MCP_CONFIG or "
             "~/.config/telethon-mcp-bridge/config.toml)",
    )
    parser.add_argument(
        "--first-run",
        action="store_true",
        help="Initialize keyring and session on first run",
    )
    parser.add_argument(
        "--migrate-to-encrypted",
        action="store_true",
        help="Migrate existing plaintext session to encrypted",
    )
    parser.add_argument(
        "--allow-plaintext",
        action="store_true",
        help="Allow plaintext session (one-shot migration runs only)",
    )
    return parser.parse_args()


async def _run(args) -> int:
    from mcp_bridge.errors import BridgeError, ConfigInvalidError

    # 1. Load config
    try:
        from mcp_bridge.config import load_config

        config_path = Path(args.config) if args.config else None
        config = load_config(config_path)
    except ConfigInvalidError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 1

    # 2. Setup logging
    try:
        from mcp_bridge.logging_setup import setup_logging
        setup_logging(config)
    except Exception as exc:
        print(f"Failed to setup logging: {exc}", file=sys.stderr)
        # Non-fatal — continue with default logging

    if args.allow_plaintext:
        __log__.warning(
            "ALLOW_PLAINTEXT flag is active — session will NOT be encrypted. "
            "This is intended for one-shot migration runs only."
        )
        print(
            "WARNING: --allow-plaintext is active. Session will not be encrypted.",
            file=sys.stderr,
        )

    # 3. Start client
    try:
        from mcp_bridge import client_holder
        await client_holder.start(config)
    except ImportError as exc:
        print(f"Platform error: {exc}", file=sys.stderr)
        return 2
    except BridgeError as exc:
        print(f"Session error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed to start client: {exc}", file=sys.stderr)
        return 2

    # 4. Run server
    try:
        from mcp_bridge.server import run_server
        await run_server(client_holder.client(), config)
        return 0
    except Exception as exc:
        __log__.exception("Runtime error")
        print(f"Runtime error: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            await client_holder.stop()
        except Exception as exc:
            __log__.warning("Error during shutdown: %s", exc)


def main() -> None:
    args = _parse_args()
    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
