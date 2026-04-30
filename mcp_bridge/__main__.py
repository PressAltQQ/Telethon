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
import os
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

    # 2b. --migrate-to-encrypted: one-shot migration, then exit
    if args.migrate_to_encrypted:
        try:
            from mcp_bridge.session.encrypted_sqlite import EncryptedSQLiteSession
            session_path = config.session_path or (
                config.base_dir / f"{config.session_name}.session"
            )
            EncryptedSQLiteSession.migrate_from_plaintext(str(session_path), config.session_name)
            print("Migration to encrypted session complete. Re-run without --migrate-to-encrypted.")
            return 0
        except Exception as exc:
            print(f"Migration failed: {exc}", file=sys.stderr)
            return 2

    # 2c. --first-run: interactive Telethon login, then exit
    if args.first_run:
        try:
            from mcp_bridge.session.encrypted_sqlite import EncryptedSQLiteSession
            from telethon import TelegramClient

            session_path = config.session_path or (
                config.base_dir / f"{config.session_name}.session"
            )
            session = EncryptedSQLiteSession(str(session_path), keystore_name=config.session_name)
            client = TelegramClient(session, config.api_id, config.api_hash)
            # Interactive login: prompts for phone number and SMS/app code
            await client.start()
            await client.disconnect()
            print("first-run login complete. Re-run without --first-run to start the MCP server.")
            return 0
        except Exception as exc:
            print(f"First-run login failed: {exc}", file=sys.stderr)
            return 2

    # 2d. Apply poll buffer size from config BEFORE client_holder.start() so
    # the update handler uses the right buffer size from the very first message.
    from mcp_bridge.tools import poll as _poll
    _poll.set_buffer_size(config.poll_buffer_size)

    # 2e. Create Correlation instance (needed for ask_user)
    from mcp_bridge.correlation import Correlation
    correlation_db_path = Path(config.base_dir) / ".correlation.db"
    correlation = Correlation(str(correlation_db_path), config=config)
    correlation.open()

    # 3. Start client (passes correlation so update handler can call match_reply)
    try:
        from mcp_bridge import client_holder
        await client_holder.start(config, correlation=correlation)
    except ImportError as exc:
        correlation.close()
        print(f"Platform error: {exc}", file=sys.stderr)
        return 2
    except BridgeError as exc:
        correlation.close()
        print(f"Session error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        correlation.close()
        print(f"Failed to start client: {exc}", file=sys.stderr)
        return 2

    # 4. Run server
    try:
        if os.environ.get("MCP_READONLY") == "1":
            from mcp_bridge.readonly_guard import install as _install_guard
            _install_guard(client_holder.client())
        from mcp_bridge.server import run_server
        await run_server(client_holder.client(), config, correlation=correlation)
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
        try:
            correlation.close()
        except Exception as exc:
            __log__.warning("Error closing correlation: %s", exc)


def main() -> None:
    args = _parse_args()
    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
