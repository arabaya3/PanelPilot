"""Tests for `app/core/logging.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

import logging

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
