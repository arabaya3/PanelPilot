"""Shared helpers for the ``pp-design/2`` tests: synthetic configurations and fixtures.

Every configuration under ``configurations/`` is synthetic test data; its
markings and labels are illustrative only (each file says so). The tests
iterate the directory, so a configuration added there is composed and checked
with no code change: ``heldout_radial`` is such a held-out case.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.tests.design.model.compat_support import DESIGNED

CONFIG_DIR = Path(__file__).parent / "configurations"

#: The held-out configuration: added as data only, never named by app code.
HELD_OUT = "heldout_radial"


def _load() -> dict[str, dict[str, Any]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CONFIG_DIR.glob("*.json"))
    }


#: Every configuration case, by file name.
CONFIGS: dict[str, dict[str, Any]] = _load()


def v1_snapshot(case: str) -> dict[str, Any]:
    """A fresh copy of a designed M1 fixture's stored v1 snapshot."""
    snapshot: dict[str, Any] = copy.deepcopy(DESIGNED[case]["stored"]["design_snapshot"])
    return snapshot


def config_case(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The v1 snapshot and a fresh copy of the configuration of a case."""
    body = CONFIGS[name]
    return v1_snapshot(body["fixture"]), copy.deepcopy(body["configuration"])
