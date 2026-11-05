"""Tests for `app/core/logging.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

import logging

import pytest

from app.core import logging as app_logging


def test_debug_level_keeps_the_model_sdks_request_bodies_out_of_the_log() -> None:
    app_logging.configure_logging(log_level="DEBUG", json_output=True)
    try:
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
        for name in app_logging.QUIET_LOGGERS:
            assert logging.getLogger(name).getEffectiveLevel() == logging.INFO
        app_logging.configure_logging(log_level="WARNING", json_output=True)
        assert logging.getLogger("openai").getEffectiveLevel() == logging.WARNING
    finally:
        app_logging.configure_logging(log_level="INFO", json_output=False)


def test_an_sdk_imported_later_with_its_debug_env_var_stays_quiet() -> None:
    """``ANTHROPIC_LOG``/``OPENAI_LOG=debug`` are read at the SDK's import, after configure."""
    import os
    import subprocess
    import sys

    script = """
import logging, sys
from app.core.logging import configure_logging
configure_logging(log_level="DEBUG", json_output=True)
from app.ai import anthropic_client
import anthropic, openai  # noqa: E401  the SDKs read their *_LOG variables here
from app.core.logging import quiet_sdk_loggers, QUIET_LOGGERS
quiet_sdk_loggers()
bad = [n for n in QUIET_LOGGERS if logging.getLogger(n).getEffectiveLevel() < logging.INFO]
bad += [n for n in ("anthropic._base_client", "openai._base_client")
        if logging.getLogger(n).isEnabledFor(logging.DEBUG)]
print("BAD", bad) if bad else print("OK")
"""
    env = {**os.environ, "ANTHROPIC_LOG": "debug", "OPENAI_LOG": "debug"}
    done = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120
    )
    assert done.stdout.strip().splitlines()[-1] == "OK", done.stdout + done.stderr


def test_creating_a_client_quiets_the_sdk_loggers_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The level an SDK set on import is undone when the app builds its client."""
    from app.ai import anthropic_client

    app_logging.configure_logging(log_level="DEBUG", json_output=True)
    try:
        logging.getLogger("anthropic").setLevel(logging.DEBUG)  # as ANTHROPIC_LOG=debug does
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        monkeypatch.setattr(anthropic_client, "_anthropic_key", lambda: "test-key")
        anthropic_client.get_anthropic_client.cache_clear()
        anthropic_client.get_anthropic_client()
        assert logging.getLogger("anthropic").level == logging.INFO
        assert logging.getLogger("httpx").level == logging.INFO
    finally:
        anthropic_client.get_anthropic_client.cache_clear()
        app_logging.configure_logging(log_level="INFO", json_output=False)
