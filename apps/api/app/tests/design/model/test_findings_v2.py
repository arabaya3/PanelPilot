"""Structural findings: each code positive and negative, and nothing engineered."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.design.model import entities, findings_v2, migrate_v2, write
from app.design.model.read import read_snapshot
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import CONFIGS, config_case, v1_snapshot


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
        bar = next(t for t in configuration["templates"] if t["ref"] == "global/synth-bar/v1")
        bar["review"]["status"] = "rejected"

    model = _composed("feeder_1p", reject)
    rejected = [
        f
        for f in _codes(model, "template_rejected")
        if f.location["template"] == "global/synth-bar/v1"
    ]
    assert rejected
    # Blocking for approval, not merely unreviewed, and never executable.
    assert all(f.severity == "blocking" and f.blocks_execution for f in rejected)
    assert not [
        f
        for f in _codes(model, "template_unreviewed")
        if f.location["template"] == "global/synth-bar/v1"
    ]


def test_a_restricted_template_is_out_of_execution_scope() -> None:
    v1, configuration = config_case("feeder_1p")
    envelope = write.snapshot_v2_from_v1(v1, configuration)
    payload = copy.deepcopy(envelope["model"])
    template = next(t for t in payload["templates"] if t["ref"] == "global/synth-mcb-1p/v1")
    template["restricted_scope"] = ["fire_pump", "life_safety"]
    payload["registry_lock"] = migrate_v2.lock_of(payload["templates"], payload["patterns"])
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


# --------------------------------------------------------------------------- #
# Severities: findings_v2 is their one source
# --------------------------------------------------------------------------- #


def test_codes_and_severities_are_one_table() -> None:
    assert tuple(findings_v2.SEVERITIES) == findings_v2.CODES
    assert set(findings_v2.SEVERITIES.values()) == {"blocking", "warning"}
    assert set(findings_v2.CODES) >= findings_v2.EXECUTION_BLOCKING
    assert set(findings_v2.CODES) >= findings_v2.UNRESOLVED
    for name in sorted(CONFIGS):
        for found in findings_v2.structural_findings(_composed(name)):
            assert found.severity == findings_v2.SEVERITIES[found.code]
            assert found.blocks_execution is (found.code in findings_v2.EXECUTION_BLOCKING)
            assert found.unresolved is (found.code in findings_v2.UNRESOLVED)


def test_none_of_the_connectivity_checks_blocks_execution() -> None:
    new = {
        "terminal_requiredness_unresolved",
        "net_unrealised",
        "net_signal_conflict",
        "wire_number_duplicate",
        "wire_attribute_assumed",
    }
    assert not new & findings_v2.EXECUTION_BLOCKING


def test_the_pe_n_distribution_data_passes_validation() -> None:
    """A configuration added as data only composes with no blocking finding."""
    model = _composed(PE_N)
    blocking = [f for f in findings_v2.structural_findings(model) if f.severity == "blocking"]
    assert blocking == []


# --------------------------------------------------------------------------- #
# Requiredness: true, false or null; explicit only when documented
# --------------------------------------------------------------------------- #

PE_N = "pe_n_distribution"
BAR = "tenant/synth-dist-bar-3way/v1"


def _bar(configuration: dict[str, Any]) -> dict[str, Any]:
    template: dict[str, Any] = next(t for t in configuration["templates"] if t["ref"] == BAR)
    return template


def _feed(required: bool | None, source: str | None = "synthetic test data") -> Any:
    def mutate(configuration: dict[str, Any]) -> None:
        bar = _bar(configuration)
        next(t for t in bar["terminals"] if t["id"] == "feed")["required"] = required
        bar["provenance"]["source"] = source

    return mutate


def test_explicitly_required_means_true_and_a_documented_source() -> None:
    def template(source: str | None) -> entities.ComponentTemplate:
        provenance = entities.Provenance(source=source, author="test suite", created="2026-10-31")
        review = entities.Review("unreviewed", None, None, (), None)
        return entities.ComponentTemplate("global/t/v1", "f", (), (), (), provenance, review, ())

    def terminal(required: bool | None) -> entities.TerminalDef:
        return entities.TerminalDef("a", "A", "k", required, None)

    documented, undocumented, blank = template("doc"), template(None), template("  ")
    assert entities.explicitly_required(documented, terminal(True))
    assert not entities.explicitly_required(documented, terminal(None))
    assert not entities.explicitly_required(documented, terminal(False))
    assert not entities.explicitly_required(undocumented, terminal(True))
    assert not entities.explicitly_required(blank, terminal(True))


def test_an_explicitly_required_open_terminal_blocks() -> None:
    model = _composed(PE_N, _feed(True))
    found = _codes(model, "unconnected_required_terminal")
    assert {f.location["terminal"] for f in found} == {"feed"}
    assert len(found) == 2
    assert all(f.severity == "blocking" for f in found)
    assert _codes(model, "terminal_requiredness_unresolved") == []


def test_an_unsaid_requiredness_stays_unresolved_and_never_blocks() -> None:
    model = _composed(PE_N, _feed(None))
    found = _codes(model, "terminal_requiredness_unresolved")
    assert {f.location["terminal"] for f in found} == {"feed"}
    assert all(f.severity == "warning" and f.unresolved for f in found)
    assert all(not f.blocks_execution for f in found)
    assert _codes(model, "unconnected_required_terminal") == []


def test_required_without_a_documented_source_is_unresolved() -> None:
    model = _composed(PE_N, _feed(True, source=None))
    assert _codes(model, "unconnected_required_terminal") == []
    found = _codes(model, "terminal_requiredness_unresolved")
    assert {f.location["terminal"] for f in found} == {"feed"}
    assert all("documents no source" in f.message for f in found)


def test_required_false_left_open_is_no_finding() -> None:
    model = _composed(PE_N, _feed(False))
    terminals = {
        f.location.get("terminal")
        for code in ("unconnected_required_terminal", "terminal_requiredness_unresolved")
        for f in _codes(model, code)
    }
    assert terminals == set()  # t3 is false too and open: no finding


# --------------------------------------------------------------------------- #
# Connectivity checks: positive and negative cases, from data
# --------------------------------------------------------------------------- #


def _drop_busbars(configuration: dict[str, Any]) -> None:
    for net in configuration["patterns"][0]["nets"]:
        net["connections"] = [c for c in net["connections"] if c["via"] != "busbar"]


def test_a_net_its_connections_do_not_join_is_unrealised() -> None:
    assert _codes(_composed(PE_N), "net_unrealised") == []
    found = _codes(_composed(PE_N, _drop_busbars), "net_unrealised")
    assert len(found) == 2
    assert all(f.severity == "blocking" and not f.blocks_execution for f in found)
    assert {f.location["groups"] for f in found} == {"2"}


def test_a_strip_point_is_not_assumed_to_join_its_sides() -> None:
    def drop_internal(configuration: dict[str, Any]) -> None:
        # Only the earth net: dropping its last-but-one entries keeps the wire indexes.
        earth = next(n for n in configuration["patterns"][0]["nets"] if n["role"] == "earth")
        earth["connections"] = [c for c in earth["connections"] if c["via"] != "internal"]

    (found,) = _codes(_composed(PE_N, drop_internal), "net_unrealised")
    assert found.location["groups"] == "3"


def test_a_signal_label_on_two_nets_of_a_board_is_a_conflict_warning() -> None:
    assert _codes(_composed(PE_N), "net_signal_conflict") == []

    def same_label(configuration: dict[str, Any]) -> None:
        # Board-scoped nets with one label are one net; per-binding ones stay apart.
        for net in configuration["patterns"][0]["nets"]:
            net.update(signal="SYN-SAME", scope="binding")

    (found,) = _codes(_composed(PE_N, same_label), "net_signal_conflict")
    assert found.severity == "warning"
    assert not found.blocks_execution
    assert found.location["signal"] == "SYN-SAME"
    assert len(found.location["nets"].split(",")) == 2
    # Per-binding nets that share a label across bindings are reported too.
    (feeder,) = _codes(_composed("feeder_1p"), "net_signal_conflict")
    assert len(feeder.location["nets"].split(",")) == 3


def _duplicate_number(configuration: dict[str, Any]) -> None:
    configuration["bindings"][0]["wires"]["neutral/2"] = {"number": "SYN-1"}


def test_a_repeated_explicit_wire_number_is_a_warning_in_its_board() -> None:
    assert _codes(_composed(PE_N), "wire_number_duplicate") == []
    model = _composed(PE_N, _duplicate_number)
    (found,) = _codes(model, "wire_number_duplicate")
    assert found.severity == "warning"
    assert not found.blocks_execution
    assert found.location["scope"] == "board"
    assert found.location["board_id"] == "DB-V"
    assert found.location["number"] == "SYN-1"
    assert len(found.location["wires"].split(",")) == 2


def test_unset_wire_numbers_are_never_duplicates() -> None:
    model = _composed("motor_dol_with_control")
    assert all(w.number.source == "unset" for w in model.wires)
    assert _codes(model, "wire_number_duplicate") == []


def test_the_wire_number_scope_is_a_parameter() -> None:
    model = _composed(PE_N, _duplicate_number)
    (found,) = [
        f
        for f in findings_v2.structural_findings(model, wire_number_scope="design")
        if f.code == "wire_number_duplicate"
    ]
    assert found.location["scope"] == "design"
    assert "board_id" not in found.location
    with pytest.raises(ValueError, match="wire number scope"):
        findings_v2.structural_findings(model, wire_number_scope="cabinet")


def test_an_assumed_wire_value_is_a_warning_naming_its_record() -> None:
    model = _composed(PE_N)
    (found,) = _codes(model, "wire_attribute_assumed")
    assert found.severity == "warning"
    assert not found.unresolved
    record = model.assumption(found.location["assumption"])
    assert record is not None
    assert record.statement


# --------------------------------------------------------------------------- #
# Patterns are reviewed like templates
# --------------------------------------------------------------------------- #


def test_an_unreviewed_pattern_blocks_execution_only() -> None:
    (found,) = _codes(_composed(PE_N), "pattern_unreviewed")
    assert found.severity == "warning"
    assert found.blocks_execution
    assert found.location["pattern"] == "tenant/synth-pe-n-distribution/v1"


def test_a_rejected_pattern_blocks_approval_and_execution() -> None:
    def reject(configuration: dict[str, Any]) -> None:
        configuration["patterns"][0]["review"].update(
            status="rejected", reviewer="synthetic reviewer", reviewed_at="2026-10-31"
        )

    model = _composed(PE_N, reject)
    (found,) = _codes(model, "pattern_rejected")
    assert found.severity == "blocking"
    assert found.blocks_execution
    assert _codes(model, "pattern_unreviewed") == []


def test_an_approved_pattern_is_no_finding() -> None:
    def approve(configuration: dict[str, Any]) -> None:
        configuration["patterns"][0]["review"].update(
            status="approved", reviewer="synthetic reviewer", reviewed_at="2026-10-31"
        )

    model = _composed(PE_N, approve)
    assert _codes(model, "pattern_unreviewed") == _codes(model, "pattern_rejected") == []
