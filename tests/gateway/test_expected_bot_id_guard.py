"""Unit tests for BasePlatformAdapter._check_expected_bot_id.

Identity guard for the EXPECTED_BOT_ID env-var protection. Prevents
double-bot presence when the same logical identity is running both
natively and in a container with mismatched tokens.

Tests exercise the helper directly on a lightweight subclass of
BasePlatformAdapter so they're free of discord/telegram library
dependencies. End-to-end wiring in the adapters is tested elsewhere.
"""

import logging
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter


class _StubAdapter(BasePlatformAdapter):
    """Minimal concrete subclass so we can instantiate the base class."""

    # All abstract methods are no-ops — we only exercise the guard helper.
    async def connect(self) -> bool:  # pragma: no cover - not exercised
        return True

    async def disconnect(self) -> None:  # pragma: no cover - not exercised
        return None

    async def send(self, *args, **kwargs):  # pragma: no cover - not exercised
        return None

    async def get_chat_info(self, *args, **kwargs):  # pragma: no cover
        return None


@pytest.fixture
def adapter(monkeypatch):
    """Build a stub adapter with the runtime-status writer stubbed out.

    ``_write_runtime_status_safe`` touches the gateway's runtime status
    file; we don't want test runs writing real files. Stubbing it leaves
    fatal-error state-setting intact so we can assert on it.
    """
    a = _StubAdapter(
        config=PlatformConfig(enabled=True, token="test"),
        platform=Platform.DISCORD,
    )
    monkeypatch.setattr(a, "_write_runtime_status_safe", lambda *a, **k: None)
    return a


@pytest.fixture
def logger_():
    return logging.getLogger("test_expected_bot_id_guard")


class TestExpectedBotIdGuard:
    def test_guard_disabled_when_env_var_unset(self, adapter, logger_, monkeypatch):
        """Unset env var → guard returns True (disabled) and no fatal error."""
        monkeypatch.delenv("DISCORD_EXPECTED_BOT_ID", raising=False)
        result = adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=12345,
            actual_label="Bot#0001",
            logger_=logger_,
        )
        assert result is True
        assert not adapter.has_fatal_error

    def test_guard_disabled_when_env_var_blank(self, adapter, logger_, monkeypatch):
        """Whitespace-only env var → treated as unset."""
        monkeypatch.setenv("DISCORD_EXPECTED_BOT_ID", "   ")
        result = adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=12345,
            actual_label="Bot#0001",
            logger_=logger_,
        )
        assert result is True
        assert not adapter.has_fatal_error

    def test_guard_passes_when_ids_match(self, adapter, logger_, monkeypatch):
        """Matching ID → guard passes, no fatal error."""
        monkeypatch.setenv("DISCORD_EXPECTED_BOT_ID", "12345")
        result = adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=12345,
            actual_label="Bot#0001",
            logger_=logger_,
        )
        assert result is True
        assert not adapter.has_fatal_error

    def test_guard_passes_with_surrounding_whitespace(self, adapter, logger_, monkeypatch):
        """Env var with surrounding whitespace still parses + matches."""
        monkeypatch.setenv("DISCORD_EXPECTED_BOT_ID", "  12345  ")
        result = adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=12345,
            actual_label="Bot#0001",
            logger_=logger_,
        )
        assert result is True
        assert not adapter.has_fatal_error

    def test_guard_fails_on_id_mismatch(self, adapter, logger_, monkeypatch):
        """Mismatched ID → guard fails, fatal error set non-retryable."""
        monkeypatch.setenv("DISCORD_EXPECTED_BOT_ID", "12345")
        result = adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=99999,
            actual_label="Bot#9999",
            logger_=logger_,
        )
        assert result is False
        assert adapter.has_fatal_error
        assert adapter.fatal_error_code == "discord_bot_id_mismatch"
        assert adapter.fatal_error_retryable is False
        assert "99999" in adapter.fatal_error_message
        assert "12345" in adapter.fatal_error_message

    def test_guard_fails_on_none_actual_id(self, adapter, logger_, monkeypatch):
        """actual_id=None → treated as mismatch (no identity available)."""
        monkeypatch.setenv("DISCORD_EXPECTED_BOT_ID", "12345")
        result = adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=None,
            actual_label="unknown",
            logger_=logger_,
        )
        assert result is False
        assert adapter.fatal_error_code == "discord_bot_id_mismatch"

    def test_guard_fails_on_non_integer_env(self, adapter, logger_, monkeypatch):
        """Non-integer env var → guard fails with invalid-format code."""
        monkeypatch.setenv("DISCORD_EXPECTED_BOT_ID", "not-an-int")
        result = adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=12345,
            actual_label="Bot#0001",
            logger_=logger_,
        )
        assert result is False
        assert adapter.has_fatal_error
        assert adapter.fatal_error_code == "discord_expected_bot_id_invalid"
        assert adapter.fatal_error_retryable is False

    def test_failure_label_included_in_message(self, adapter, logger_, monkeypatch):
        """actual_label appears verbatim in the failure message for diag."""
        monkeypatch.setenv("DISCORD_EXPECTED_BOT_ID", "12345")
        adapter._check_expected_bot_id(
            env_var="DISCORD_EXPECTED_BOT_ID",
            actual_id=99999,
            actual_label="@some_other_bot",
            logger_=logger_,
        )
        assert "@some_other_bot" in adapter.fatal_error_message

    def test_platform_prefix_uses_platform_value(self, monkeypatch, logger_):
        """Fatal error codes are prefixed with the platform's value attr."""
        a = _StubAdapter(
            config=PlatformConfig(enabled=True, token="test"),
            platform=Platform.TELEGRAM,
        )
        monkeypatch.setattr(a, "_write_runtime_status_safe", lambda *a, **k: None)
        monkeypatch.setenv("TELEGRAM_EXPECTED_BOT_ID", "12345")
        a._check_expected_bot_id(
            env_var="TELEGRAM_EXPECTED_BOT_ID",
            actual_id=99999,
            actual_label="@other",
            logger_=logger_,
        )
        assert a.fatal_error_code == "telegram_bot_id_mismatch"
