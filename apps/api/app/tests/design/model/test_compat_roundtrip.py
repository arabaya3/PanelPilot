"""M1 compatibility: every stored fixture survives the versioned model unchanged.

For each snapshot the Phase 1 pipeline stores (``fixtures/*.json``):

* ``from_v1`` then ``to_v1`` gives back the same design, compared both as
  ``model_dump`` and leaf by leaf with Decimals by their exact text;
* the canonical bytes, and so ``sha256_of``, are identical;
* the hash recorded at save still verifies once read through the model, and
  ``serialize`` reproduces it from the domain model alone;
* the fixture really is what the pipeline stores (its hash is the canonical
  hash of its snapshot, and it validates as a ``DesignProject``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.design import snapshot
from app.design.model import serialize, versions
from app.design.model.convert_v1 import from_v1, to_v1
from app.design.model.read import read_snapshot
from app.models.schemas.design import DesignProject, DeviceKind, MotorStarter
from app.tests.design.model.compat_support import DESIGNED, FIXTURES, strict_shape

CASES = sorted(DESIGNED)


def _stored(case: str) -> dict[str, Any]:
    stored: dict[str, Any] = DESIGNED[case]["stored"]
    return stored


def test_fixture_set_covers_the_brief() -> None:
    """The fixtures cover what M1 must keep: if one is dropped, this says which."""
    expected = {
        "residential",
        "multi_board",
        "motors",
        "plc",
        "arabic",
        "hebrew",
        "max_length",
        "decimal_inputs",
        "decimal_edges",
        "optional_absent",
        "optional_present",
        "empty_lists",
        "findings",
        "refused",
        "every_device_kind",
    }
    assert expected <= set(FIXTURES)
    assert {name for name in FIXTURES if name.startswith("market_")}, "no market fixture"


def test_markets_are_all_covered() -> None:
    from app.design import markets

    assert {f"market_{code.lower()}" for code in markets.MARKETS} <= set(DESIGNED)


def test_every_device_kind_and_starter_is_stored_somewhere() -> None:
    kinds = {
        device["kind"]
        for body in DESIGNED.values()
        for board in body["stored"]["design_snapshot"]["boards"]
        for device in board["devices"]
    }
    assert {kind.value for kind in DeviceKind} <= kinds
    starters = {
        circuit.get("starter")
        for body in DESIGNED.values()
        for board in body["stored"]["design_snapshot"]["boards"]
        for circuit in board["circuits"]
    }
    assert {starter.value for starter in MotorStarter} <= starters


def test_sub_mains_and_blocking_findings_are_present() -> None:
    boards = DESIGNED["multi_board"]["stored"]["design_snapshot"]["boards"]
    assert {b["fed_from"] for b in boards} >= {"MDB", "SMDB-2"}
    blocking = [
        f for f in DESIGNED["findings"]["stored"]["findings"] if f["severity"] == "blocking"
    ]
    assert blocking, "the findings fixture must hold a blocking finding"
    refused = FIXTURES["refused"]["stored"]
    assert refused.get("design_snapshot") is None
    assert [f["code"] for f in refused["findings"]] == ["design_refused"]


@pytest.mark.parametrize("case", CASES)
def test_fixture_is_a_phase1_stored_snapshot(case: str) -> None:
    stored = _stored(case)
    assert snapshot.sha256_of(stored["design_snapshot"]) == stored["snapshot_sha256"]
    assert versions.SCHEMA_KEY not in stored["design_snapshot"]
    # Strict v1 validation, then dumped as a save dumps it: the same JSON.
    project = DesignProject.model_validate(stored["design_snapshot"])
    assert project.model_dump(mode="json") == stored["design_snapshot"]


@pytest.mark.parametrize("case", CASES)
def test_round_trip_is_lossless(case: str) -> None:
    stored = _stored(case)["design_snapshot"]
    original = DesignProject.model_validate(stored)
    back = to_v1(from_v1(original))
    assert back == original
    assert back.model_dump() == original.model_dump()
    assert strict_shape(back.model_dump()) == strict_shape(original.model_dump())
    assert back.model_dump(mode="json") == stored


@pytest.mark.parametrize("case", CASES)
def test_round_trip_from_stored_dict(case: str) -> None:
    """``from_v1`` takes the stored JSON itself, as a reader of the column would."""
    stored = _stored(case)["design_snapshot"]
    assert to_v1(from_v1(stored)).model_dump(mode="json") == stored


@pytest.mark.parametrize("case", CASES)
def test_canonical_bytes_and_hash_are_unchanged(case: str) -> None:
    body = _stored(case)
    stored = body["design_snapshot"]
    model = from_v1(stored)
    assert serialize.canonical_v1(model) == snapshot.canonical_json(stored)
    assert serialize.sha256_v1(model) == body["snapshot_sha256"]
    assert serialize.to_stored_v1(model) == stored
    rebuilt = to_v1(model).model_dump(mode="json")
    assert snapshot.canonical_json(rebuilt) == snapshot.canonical_json(stored)
    assert snapshot.sha256_of(rebuilt) == body["snapshot_sha256"]


@pytest.mark.parametrize("case", CASES)
def test_recorded_hash_verifies_after_reading_through_the_model(case: str) -> None:
    body = _stored(case)
    read = read_snapshot(body["design_snapshot"])
    assert read.schema == versions.V1
    assert read.project.model_dump(mode="json") == body["design_snapshot"]
    assert serialize.sha256_v1(read.model) == body["snapshot_sha256"]
    assert snapshot.sha256_of(read.project.model_dump(mode="json")) == body["snapshot_sha256"]


@pytest.mark.parametrize("case", CASES)
def test_reading_does_not_mutate_the_stored_value(case: str) -> None:
    import copy

    stored = copy.deepcopy(_stored(case)["design_snapshot"])
    before = snapshot.canonical_json(stored)
    read_snapshot(stored)
    assert snapshot.canonical_json(stored) == before


def test_decimal_edges_keep_their_exact_text() -> None:
    """Values at each schema bound, and exotic unbounded texts, are stored as written."""
    from app.models.schemas.design import Cable, Device
    from app.tests.design.model.compat_support import bounds_of, valid_edges
    from app.tests.design.model.fixtures.generate import (
        CABLE_FIELDS,
        DEVICE_FIELDS,
        UNBOUNDED_EDGES,
    )

    stored = _stored("decimal_edges")["design_snapshot"]
    board = stored["boards"][0]
    for model, names, items in (
        (Device, DEVICE_FIELDS, board["devices"]),
        (Cable, CABLE_FIELDS, board["cables"]),
    ):
        for name in names:
            texts = {item.get(name) for item in items if item["id"].startswith(f"dec-{name}-")}
            expected = {str(v) for v in valid_edges(bounds_of(model, name))}
            bound = bounds_of(model, name)
            assert str(bound.le) in texts, f"{name}: the upper bound itself is not held"
            assert expected <= texts, name
    widths = {p["width_mm"] for p in stored["parts"] if p["key"].startswith("dec-part-")}
    # Decimal("0.000000001") is stored as str() gives it: "1E-9".
    assert {str(Decimal(t)) for t in UNBOUNDED_EDGES} <= widths
    back = to_v1(from_v1(stored)).model_dump(mode="json")
    assert back == stored


def test_unicode_and_rtl_names_round_trip_byte_for_byte() -> None:
    for case, needle in (("arabic", "مشروع"), ("hebrew", "פרויקט")):
        stored = _stored(case)["design_snapshot"]
        assert needle in stored["info"]["name"]
        raw = serialize.canonical_v1(from_v1(stored))
        assert needle.encode("utf-8") in raw
        assert raw == snapshot.canonical_json(stored)
