"""``write``: v2 snapshots are checked before they are returned."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.design.model import compose, migrate_v2, versions, write
from app.tests.design.model.v2_support import config_case, v1_snapshot

MODEL_DIR = Path(migrate_v2.__file__).parent
API_ROOT = MODEL_DIR.parents[2]
PURE_MODULES = ("entities.py", "findings_v2.py", "domain.py")
FRAMEWORKS = ("pydantic", "fastapi", "sqlalchemy", "starlette")


def _composed(name: str = "feeder_1p") -> dict[str, Any]:
    v1, configuration = config_case(name)
    return write.snapshot_v2_from_v1(v1, configuration)


def test_write_refuses_to_envelope_an_invalid_payload() -> None:
    payload = _composed()["model"]
    payload["components"].reverse()
    with pytest.raises(versions.SnapshotUnreadableError):
        write.envelope(payload)


def test_without_a_configuration_the_snapshot_is_the_migration() -> None:
    v1 = v1_snapshot("residential")
    envelope = write.snapshot_v2_from_v1(v1)
    assert envelope == {"schema": versions.V2, "model": migrate_v2.migrate_1_to_2(v1)}


def test_with_a_configuration_the_snapshot_is_the_composition() -> None:
    v1, configuration = config_case("feeder_1p")
    envelope = write.snapshot_v2_from_v1(v1, configuration)
    assert envelope["model"] == compose.compose(v1, configuration)
