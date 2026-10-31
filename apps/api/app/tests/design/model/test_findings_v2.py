"""Structural findings: each code positive and negative, and nothing engineered."""

from __future__ import annotations

import copy
from typing import Any

from app.design.model import entities, findings_v2, migrate_v2, write
from app.design.model.read import read_snapshot
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import config_case, v1_snapshot


def _model(envelope: dict[str, Any]) -> entities.DesignModelV2:
    model = read_snapshot(envelope).v2
    assert model is not None
    return model


def _composed(name: str, mutate: Any = None) -> entities.DesignModelV2:
    v1, configuration = config_case(name)
    if mutate is not None:
        mutate(configuration)
    return _model(write.snapshot_v2_from_v1(v1, configuration))


def _codes(model: entities.DesignModelV2, code: str) -> list[findings_v2.StructuralFinding]:
    return [f for f in findings_v2.structural_findings(model) if f.code == code]


def _approve_all(configuration: dict[str, Any]) -> None:
    for template in configuration["templates"]:
        template["review"].update(
            status="approved", reviewer="synthetic reviewer", reviewed_at="2026-10-31"
        )


def test_every_finding_is_integrity_sourced_and_known() -> None:
    for name in ("feeder_1p", "io_marshalling", "motor_dol_with_control"):
        for finding in findings_v2.structural_findings(_composed(name)):
            assert finding.source == "integrity"
            assert finding.code in findings_v2.CODES
            assert finding.severity in {"blocking", "warning"}


def test_findings_are_deterministic() -> None:
    first = findings_v2.structural_findings(_composed("feeder_1p"))
    assert first == findings_v2.structural_findings(_composed("feeder_1p"))


def test_every_migrated_component_is_unreviewed_and_an_execution_blocker() -> None:
    for case in sorted(DESIGNED):
        model = _model(write.snapshot_v2_from_v1(v1_snapshot(case)))
        unreviewed = _codes(model, "template_unreviewed")
        assert {f.location["component"] for f in unreviewed} == {c.id for c in model.components}
        assert list(findings_v2.execution_blockers(model)) == unreviewed
        assert _codes(model, "wire_attribute_unset") == []
        assert _codes(model, "unconnected_required_terminal") == []


def test_an_approved_template_is_no_finding_but_legacy_ones_still_are() -> None:
    model = _composed("feeder_1p", _approve_all)
    unreviewed = {f.location["component"] for f in _codes(model, "template_unreviewed")}
    legacy = {c.id for c in model.components if c.template_ref.startswith("global/legacy")}
    assert unreviewed == legacy
    assert legacy  # the incomer and the residual current device keep legacy templates


def test_a_rejected_template_is_a_finding() -> None:
    def reject(configuration: dict[str, Any]) -> None:
        _approve_all(configuration)
        bar = next(t for t in configuration["templates"] if t["ref"] == "global/synth-bar@1")
        bar["review"]["status"] = "rejected"

    model = _composed("feeder_1p", reject)
    rejected = [
        f
        for f in _codes(model, "template_unreviewed")
        if f.location["template"] == "global/synth-bar@1"
    ]
    assert rejected
    assert all(f.blocks_execution for f in rejected)


def test_a_restricted_template_is_out_of_execution_scope() -> None:
    v1, configuration = config_case("feeder_1p")
    envelope = write.snapshot_v2_from_v1(v1, configuration)
    payload = copy.deepcopy(envelope["model"])
    template = next(t for t in payload["templates"] if t["ref"] == "global/synth-mcb-1p@1")
    template["restricted_scope"] = ["fire_pump", "life_safety"]
    payload["registry_lock"] = migrate_v2.lock_of(payload["templates"])
    model = _model(write.envelope(payload))
    out = _codes(model, "out_of_execution_scope")
    assert len(out) == 3
    assert all(f.blocks_execution and f.severity == "blocking" for f in out)
    assert all(f in findings_v2.execution_blockers(model) for f in out)
    assert _codes(_model(envelope), "out_of_execution_scope") == []


def test_unconnected_required_terminals_are_findings() -> None:
    model = _composed("feeder_1p")
    unconnected = _codes(model, "unconnected_required_terminal")
    assert {f.location["terminal"] for f in unconnected} == {"in1"}
    assert len(unconnected) == 3
    assert all(not f.blocks_execution for f in unconnected)
    assert _codes(_composed("heldout_radial"), "unconnected_required_terminal") == []


def test_an_optional_terminal_left_open_is_no_finding() -> None:
    model = _composed("io_marshalling")
    unconnected = _codes(model, "unconnected_required_terminal")
    assert {f.location["terminal"] for f in unconnected} == {"m"}  # di3 is optional


def test_every_unset_wire_attribute_is_a_warning_and_a_given_one_is_not() -> None:
    model = _composed("feeder_1p")
    unset = _codes(model, "wire_attribute_unset")
    assert len(unset) == 3 * len(model.wires) - 1
    assert all(f.severity == "warning" for f in unset)
    given = next(w for w in model.wires if w.number.source == "data")
    assert {f.location["attribute"] for f in unset if f.location["wire"] == given.id} == {
        "cross_section_mm2",
        "colour",
    }
