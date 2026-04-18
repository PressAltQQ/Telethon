"""Tests for mcp_bridge/logging_setup.py."""
import dataclasses
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

import pytest

from mcp_bridge.logging_setup import _JsonLinesFormatter, hash_chat_id, log_tool, setup_logging


@dataclasses.dataclass
class FakeConfig:
    log_level: str = "INFO"
    log_file: Path = Path("/tmp/test_bridge.log")


class TestSetupLogging:
    def teardown_method(self):
        # Reset loggers after each test
        for name in ["mcp", "telethon.network", "telethon.extensions"]:
            lg = logging.getLogger(name)
            lg.setLevel(logging.NOTSET)

    def test_mcp_logger_at_warning_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TELETHON_MCP_DEBUG_PROTOCOL", raising=False)
        cfg = FakeConfig(log_file=tmp_path / "bridge.log")
        setup_logging(cfg)
        mcp_logger = logging.getLogger("mcp")
        assert mcp_logger.level == logging.WARNING

    def test_mcp_logger_at_debug_when_env_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELETHON_MCP_DEBUG_PROTOCOL", "1")
        cfg = FakeConfig(log_file=tmp_path / "bridge.log")
        setup_logging(cfg)
        mcp_logger = logging.getLogger("mcp")
        assert mcp_logger.level == logging.DEBUG

    def test_telethon_network_at_warning_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TELETHON_MCP_DEBUG_PROTOCOL", raising=False)
        cfg = FakeConfig(log_file=tmp_path / "bridge.log")
        setup_logging(cfg)
        net_logger = logging.getLogger("telethon.network")
        assert net_logger.level == logging.WARNING

    def test_telethon_network_at_debug_when_env_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELETHON_MCP_DEBUG_PROTOCOL", "1")
        cfg = FakeConfig(log_file=tmp_path / "bridge.log")
        setup_logging(cfg)
        net_logger = logging.getLogger("telethon.network")
        assert net_logger.level == logging.DEBUG

    def test_telethon_extensions_gated(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TELETHON_MCP_DEBUG_PROTOCOL", raising=False)
        cfg = FakeConfig(log_file=tmp_path / "bridge.log")
        setup_logging(cfg)
        ext_logger = logging.getLogger("telethon.extensions")
        assert ext_logger.level == logging.WARNING

    def test_log_file_created(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TELETHON_MCP_DEBUG_PROTOCOL", raising=False)
        log_path = tmp_path / "sub" / "bridge.log"
        cfg = FakeConfig(log_file=log_path)
        setup_logging(cfg)
        # Log something to trigger file creation
        logging.getLogger("test").info("test message")
        assert log_path.exists()


class TestHashChatId:
    def test_returns_16_hex_chars(self):
        key = os.urandom(32)
        result = hash_chat_id(12345, key)
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_consistent_for_same_inputs(self):
        key = os.urandom(32)
        result1 = hash_chat_id(12345, key)
        result2 = hash_chat_id(12345, key)
        assert result1 == result2

    def test_different_chat_ids_produce_different_hashes(self):
        key = os.urandom(32)
        r1 = hash_chat_id(12345, key)
        r2 = hash_chat_id(99999, key)
        assert r1 != r2

    def test_different_keys_produce_different_hashes(self):
        key1 = b"a" * 32
        key2 = b"b" * 32
        r1 = hash_chat_id(12345, key1)
        r2 = hash_chat_id(12345, key2)
        assert r1 != r2

    def test_non_reversible_short_output(self):
        """8-byte hex output cannot naively reverse to the original chat_id."""
        key = os.urandom(32)
        result = hash_chat_id(12345, key)
        # The hex string is 16 chars, not the full SHA256 - it's truncated
        assert result != str(12345)
        assert len(result) == 16  # 8 bytes = 16 hex chars

    def test_uses_hmac_sha256(self):
        """Verify the implementation actually uses HMAC-SHA256."""
        key = b"testkey" + b"\x00" * 25  # 32 bytes
        chat_id = 42
        expected = hmac.new(key, str(chat_id).encode(), hashlib.sha256).digest()[:8].hex()
        assert hash_chat_id(chat_id, key) == expected

    def test_uniform_for_all_chat_types(self):
        """Private (-), group (+), channel IDs all hashed the same way."""
        key = os.urandom(32)
        # private chat (positive), group (negative), channel (large negative)
        h1 = hash_chat_id(12345, key)
        h2 = hash_chat_id(-12345, key)
        h3 = hash_chat_id(-1001234567890, key)
        # They should all be valid 16-char hex strings
        for h in [h1, h2, h3]:
            assert len(h) == 16
        # And they should all be different
        assert len({h1, h2, h3}) == 3


class TestLogTool:
    def test_yields_context_with_outcome(self):
        with log_tool("test_tool", "aabbccdd11223344", 12345) as ctx:
            ctx.outcome = "ok"
        assert ctx.outcome == "ok"

    def test_outcome_defaults_to_unknown(self):
        with log_tool("test_tool", None, 12345) as ctx:
            pass
        assert ctx.outcome == "unknown"


class TestJsonLinesFormatterStructuredFields:
    def test_tool_invocation_log_contains_all_five_fields(self):
        """Formatter must emit tool, chat_id_hashed, pid, outcome, duration_ms as top-level JSON fields."""
        formatter = _JsonLinesFormatter()

        logger = logging.getLogger("mcp_bridge.tools")
        record = logger.makeRecord(
            name="mcp_bridge.tools",
            level=logging.INFO,
            fn="",
            lno=0,
            msg="tool_invocation",
            args=(),
            exc_info=None,
            extra={
                "tool": "download_file",
                "chat_id_hashed": "aabbccdd11223344",
                "pid": 12345,
                "outcome": "ok",
                "duration_ms": 42.5,
            },
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["tool"] == "download_file"
        assert data["chat_id_hashed"] == "aabbccdd11223344"
        assert data["pid"] == 12345
        assert data["outcome"] == "ok"
        assert data["duration_ms"] == 42.5
