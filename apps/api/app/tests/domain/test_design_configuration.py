"""Client configurations: bounded, never claiming a review, tenant refs only; the summary.

Database-free: the checks and the composition run on the committed synthetic
fixtures and configurations (``app/tests/design/model``).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.design.model import findings_v2, versions
from app.design.model.read import read_snapshot
from app.domain import design_configuration, design_projects
from app.tests.design.model.v2_support import CONFIGS, config_case

LIMITS = design_configuration.LIMITS

#: The configurations a client could send: every definition in them is tenant scope.
TENANT_CASES = sorted(
    name
    for name, body in CONFIGS.items()
    if all(t["ref"].startswith("tenant/") for t in body["configuration"]["templates"])
    and all(p["ref"].startswith("tenant/") for p in body["configuration"]["patterns"])
)


def _code(configuration: Any) -> tuple[str, dict[str, Any]]:
    with pytest.raises(ValidationError) as caught:
        design_configuration.check_configuration(configuration)
    return str(caught.value.code), dict(caught.value.params)


def _config(name: str = "pe_n_distribution") -> dict[str, Any]:
    return config_case(name)[1]


def test_the_data_only_configurations_are_tenant_cases() -> None:
    assert {"pe_n_distribution", "io_marshalling", "heldout_radial"} <= set(TENANT_CASES)


@pytest.mark.parametrize("name", TENANT_CASES)
def test_a_tenant_configuration_passes_the_checks_and_composes(name: str) -> None:
    v1, configuration = config_case(name)
    design_configuration.check_configuration(configuration)
    stored = design_configuration.compose_snapshot(v1, configuration)
    assert read_snapshot(stored).schema == versions.V2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "approved"),
        ("status", "rejected"),
        ("reviewer", "Someone"),
        ("reviewed_at", "2026-10-31"),
        ("standards", ["SOME-STANDARD"]),
        ("scope_note", "reviewed for everything"),
    ],
)
@pytest.mark.parametrize("kind", ["templates", "patterns"])
def test_a_submitted_definition_never_claims_a_review(kind: str, field: str, value: Any) -> None:
    configuration = _config()
    configuration[kind][0]["review"][field] = value
    code, params = _code(configuration)
    assert code == "configuration_claims_review"
    assert field in params["fields"].split(",")


def test_a_client_claimed_approved_template_is_refused_as_a_whole_decision() -> None:
    configuration = _config()
    configuration["templates"][0]["review"].update(
        {"status": "approved", "reviewer": "A. Claimant", "reviewed_at": "2026-10-31"}
    )
    code, params = _code(configuration)
    assert code == "configuration_claims_review"
    assert set(params["fields"].split(",")) == {"status", "reviewer", "reviewed_at"}


@pytest.mark.parametrize("kind", ["templates", "patterns"])
@pytest.mark.parametrize(
    "ref", ["global/legacy-contactor/v1", "global/synth-anything/v1", "other/x/v1"]
)
def test_a_submitted_definition_may_not_use_a_reserved_ref(kind: str, ref: str) -> None:
    configuration = _config()
    configuration[kind][0]["ref"] = ref
    code, params = _code(configuration)
    assert code == "configuration_ref_reserved"
    assert params["ref"] == ref


def test_the_shipped_legacy_templates_cannot_be_resubmitted() -> None:
    from app.design.model import migrate_v2

    configuration = _config()
    configuration["templates"].append(
        copy.deepcopy(next(iter(migrate_v2.LEGACY_TEMPLATES.values())))
    )
    assert _code(configuration)[0] == "configuration_ref_reserved"


def test_a_restricted_scope_template_is_refused_by_the_composer() -> None:
    v1, configuration = config_case("pe_n_distribution")
    configuration["templates"][0]["restricted_scope"] = ["life_safety"]
    design_configuration.check_configuration(configuration)
    with pytest.raises(ValidationError) as caught:
        design_configuration.compose_snapshot(v1, configuration)
    assert caught.value.code == "configuration_refused"
    assert caught.value.params["refusal"] == "restricted_scope"


@pytest.mark.parametrize(
    ("build", "limit_of"),
    [
        (lambda: {"x": "a" * (LIMITS.max_text + 1)}, "text length"),
        (lambda: {"a" * (LIMITS.max_text + 1): 1}, "text length"),
        (lambda: [1] * (LIMITS.max_values + 1), "values"),
        (lambda: {"templates": [{}] * (LIMITS.max_templates + 1)}, "templates"),
        (lambda: {"patterns": [{}] * (LIMITS.max_patterns + 1)}, "patterns"),
        (lambda: {"strips": [{}] * (LIMITS.max_strips + 1)}, "strips"),
        (lambda: {"components": [{}] * (LIMITS.max_components + 1)}, "components"),
        (lambda: {"bindings": [{}] * (LIMITS.max_bindings + 1)}, "bindings"),
        (lambda: {"y": ["b" * 1000] * 300}, "bytes"),
    ],
)
def test_oversized_configurations_are_refused(build: Any, limit_of: str) -> None:
    code, params = _code(build())
    assert code == "configuration_too_large"
    assert params["limit_of"] == limit_of


def test_deep_nesting_is_refused_without_recursion() -> None:
    deep: Any = {}
    for _ in range(100_000):
        deep = {"n": deep}
    code, params = _code(deep)
    assert (code, params["limit_of"]) == ("configuration_too_large", "depth")


def test_an_oversized_template_or_pattern_is_refused() -> None:
    configuration = _config()
    configuration["templates"][0]["terminals"] = [{}] * (LIMITS.max_template_items + 1)
    assert _code(configuration)[1]["limit_of"] == "template size"
    configuration = _config()
    configuration["patterns"][0]["points"] = [{}] * (LIMITS.max_pattern_items + 1)
    assert _code(configuration)[1]["limit_of"] == "pattern size"


def test_a_composition_refusal_keeps_the_composers_code() -> None:
    v1, configuration = config_case("pe_n_distribution")
    configuration["bindings"][0]["pattern"] = "tenant/no-such-pattern/v1"
    with pytest.raises(ValidationError) as caught:
        design_configuration.compose_snapshot(v1, configuration)
    assert caught.value.code == "configuration_refused"
    assert caught.value.params["refusal"] == "unknown_pattern"


def test_the_summary_shows_every_value_with_its_source_and_every_finding() -> None:
    v1, configuration = config_case("pe_n_distribution")
    model = read_snapshot(design_configuration.compose_snapshot(v1, configuration)).v2
    assert model is not None
    shown = design_configuration.summary(model, design_projects._V2_CONFIRM)
    sources = {
        cell.source
        for wire in shown.wires
        for cell in (wire.number, wire.cross_section_mm2, wire.colour)
    }
    assert sources == {"user", "data", "assumption", "unresolved"}
    assumed = [w.colour for w in shown.wires if w.colour.source == "assumption"]
    assert assumed
    assert all(cell.assumption_statement for cell in assumed)
    assert {f.code for f in shown.findings} == {
        f.code for f in findings_v2.structural_findings(model)
    }
    unresolved = [t for t in shown.terminals if t.required.source == "unresolved"]
    assert unresolved
    for found in shown.findings:
        assert found.blocks_approval == (found.severity == "blocking")
        assert found.blocks_execution == (found.code in findings_v2.EXECUTION_BLOCKING)
        assert found.unresolved == (found.code in findings_v2.UNRESOLVED)
    assert {d.review_status for d in shown.definitions} == {"unreviewed"}
    assert len(shown.components) == len(model.components)
    assert (
        sum(len(s.points) for s in shown.strips)
        == CONFIGS["pe_n_distribution"]["expect"]["strip_points"]
    )
    assert len(shown.nets) == len(model.nets)
    assert len(shown.connections) == len(model.connections)
