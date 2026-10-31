"""``migrate_v2``: every M1 fixture migrates to strict v2 and projects back unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.design import snapshot
from app.design.model import codec_v2, migrate_v2, versions, write
from app.design.model.read import project_to_v1, read_snapshot
from app.models.schemas.design import DeviceKind
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import v1_snapshot

MODEL_DIR = Path(migrate_v2.__file__).parent
API_ROOT = MODEL_DIR.parents[2]
PURE_MODULES = ("entities.py", "findings_v2.py", "domain.py")
FRAMEWORKS = ("pydantic", "fastapi", "sqlalchemy", "starlette")


# --------------------------------------------------------------------------- #
# Versions and the frozen legacy templates
# --------------------------------------------------------------------------- #


def test_v2_is_enveloped_and_migrates_from_v1() -> None:
    assert versions.V2 == "pp-design/2"
    assert versions.V2 in versions.ENVELOPED
    assert versions.MIGRATIONS[(versions.V1, versions.V2)] is migrate_v2.migrate_1_to_2
    assert versions.WRITTEN_BY_DEFAULT == versions.V1


def test_one_legacy_template_per_v1_device_kind_and_nothing_else() -> None:
    assert set(migrate_v2.LEGACY_TEMPLATES) == {kind.value for kind in DeviceKind}
    assert [p.name for p in (MODEL_DIR / "templates").iterdir()] == ["legacy_v1_device_kinds.json"]
    for kind, raw in migrate_v2.LEGACY_TEMPLATES.items():
        template = codec_v2.parse_template(raw)
        assert template.ref == f"global/legacy-{kind}/v1" == migrate_v2.legacy_ref(kind)
        assert template.function == kind
        assert template.terminals == ()
        assert template.parameters == ()
        assert template.review.status == "unreviewed"
        assert template.review.standards == ()
        assert template.restricted_scope == ()
        assert "pp-design/1" in template.provenance.source
        assert codec_v2.dump_template(template) == raw


# --------------------------------------------------------------------------- #
# Migration: every M1 fixture
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", sorted(DESIGNED))
def test_every_fixture_migrates_to_strict_v2_and_projects_back_byte_for_byte(case: str) -> None:
    stored = DESIGNED[case]["stored"]
    v1 = v1_snapshot(case)
    before = json.dumps(v1, sort_keys=True)
    envelope = write.snapshot_v2_from_v1(v1)
    assert json.dumps(v1, sort_keys=True) == before, "the input is not changed"
    assert set(envelope) == {"schema", "model"}

    read = read_snapshot(envelope)
    assert read.schema == versions.V2
    assert read.v2 is not None
    projected = project_to_v1(read.v2)
    assert projected == stored["design_snapshot"]
    assert snapshot.canonical_json(projected) == snapshot.canonical_json(stored["design_snapshot"])
    assert snapshot.sha256_of(projected) == stored["snapshot_sha256"]
    assert read.project.model_dump(mode="json") == stored["design_snapshot"]

    devices = [(b.id, d) for b in read.v2.design.boards for d in b.devices]
    assert len(read.v2.components) == len(devices)
    for board_id, device in devices:
        component = next(
            c
            for c in read.v2.components
            if (c.board_id, c.legacy_device_id) == (board_id, device.id)
        )
        assert component.template_ref == migrate_v2.legacy_ref(device.kind)
        assert component.designation == device.designation
    assert read.v2.strips == ()
    assert read.v2.nets == ()
    assert read.v2.connections == ()
    assert read.v2.wires == ()
    assert read.v2.assumptions == ()


@pytest.mark.parametrize("case", sorted(DESIGNED))
def test_migration_is_deterministic_and_the_v1_read_is_unchanged(case: str) -> None:
    first = versions.migrate(v1_snapshot(case), versions.V1, versions.V2)
    second = versions.migrate(v1_snapshot(case), versions.V1, versions.V2)
    assert snapshot.canonical_json(first) == snapshot.canonical_json(second)
    stored = DESIGNED[case]["stored"]
    assert read_snapshot(stored["design_snapshot"]).schema == versions.V1
    assert snapshot.sha256_of(stored["design_snapshot"]) == stored["snapshot_sha256"]


def test_a_migrated_model_pins_its_hash() -> None:
    envelope = write.snapshot_v2_from_v1(v1_snapshot("residential"))
    assert snapshot.sha256_of(envelope) == PINNED_MIGRATED_RESIDENTIAL


PINNED_MIGRATED_RESIDENTIAL = "049f97a20875f85cb91af6eea8e306c269e35e403d7d4740abef885e537f2144"
