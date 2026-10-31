"""M2a, without a database: the write switch in ``_validate`` and the v2 findings mapping.

* Switch off, every committed M1 fixture designs to exactly the snapshot,
  hash, profile hash and findings it was committed with: v1 bytes unchanged.
* Switch on, the same request stores a v2 envelope whose findings are the v1
  findings unchanged, followed by the v2 structural ones only.
* The mapping: unreviewed and out-of-scope templates are warnings to confirm
  (never approval blockers) and execution blockers; a required terminal in no
  net blocks approval; an unset wire attribute is a warning.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import MappingProxyType
from typing import Any

import pytest

from app.core.config import get_settings
from app.design import CATALOGUE_VERSION, RULES_VERSION, integrity, snapshot
from app.design.model import findings_v2, versions
from app.design.model.read import read_snapshot
from app.domain import design_projects
from app.models.schemas.design import (
    DesignFinding,
    ExportStatus,
    FindingSeverity,
    ProjectDesignRequest,
)
from app.tests.design.model.compat_support import DESIGNED


@pytest.fixture(name="schema")
def _schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    def set_to(value: str) -> None:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", value)
        get_settings.cache_clear()

    yield set_to
    get_settings.cache_clear()


def _current(body: dict[str, Any]) -> bool:
    stored = body["stored"]
    return (stored["rules_version"], stored["catalogue_version"]) == (
        RULES_VERSION,
        CATALOGUE_VERSION,
    )


@pytest.mark.parametrize("case", sorted(DESIGNED))
def test_switch_off_designs_every_fixture_to_its_committed_bytes(schema: Any, case: str) -> None:
    body = DESIGNED[case]
    if body["source"] != "pipeline" or not _current(body):
        pytest.skip("not a current pipeline fixture")
    schema("pp-design/1")
    made = design_projects._validate(ProjectDesignRequest.model_validate(body["request"]))
    stored = body["stored"]
    assert made["design_snapshot"] == stored["design_snapshot"]
    assert made["snapshot_sha256"] == stored["snapshot_sha256"]
    assert made["profile_sha256"] == stored["profile_sha256"]
    assert made["findings"] == stored["findings"]


@pytest.mark.parametrize("case", sorted(DESIGNED))
def test_switch_on_adds_only_v2_findings_after_the_v1_ones(schema: Any, case: str) -> None:
    body = DESIGNED[case]
    if body["source"] != "pipeline" or not _current(body):
        pytest.skip("not a current pipeline fixture")
    request = ProjectDesignRequest.model_validate(body["request"])
    schema("pp-design/1")
    v1 = design_projects._validate(request)
    schema("pp-design/2")
    v2 = design_projects._validate(request)
    assert v2["design_snapshot"]["schema"] == versions.V2
    assert v2["snapshot_sha256"] == snapshot.sha256_of(v2["design_snapshot"])
    assert v2["design_snapshot"]["model"]["design"] == v1["design_snapshot"]
    assert v2["profile_sha256"] == v1["profile_sha256"]
    assert v2["findings"][: len(v1["findings"])] == v1["findings"]
    extra = v2["findings"][len(v1["findings"]) :]
    assert {found["code"] for found in extra} <= set(findings_v2.CODES)
    # Legacy templates are all unreviewed: every v2 revision says so.
    assert "template_unreviewed" in {found["code"] for found in extra}
    # And none of it blocks design-review approval.
    assert not integrity.blocking([DesignFinding.model_validate(f) for f in extra])


@pytest.mark.parametrize("case", sorted(DESIGNED))
def test_a_v1_read_is_classified_as_before(case: str) -> None:
    """Adding v2 findings changes no v1 revision's classification, so none goes stale."""
    stored = DESIGNED[case]["stored"]
    read = read_snapshot(stored["design_snapshot"])
    assert read.v2 is None
    request = ProjectDesignRequest.model_validate(DESIGNED[case]["request"])
    conditions = {board.name: board.conditions for board in request.boards}
    assert design_projects._design_findings(
        read.project, conditions, None
    ) == integrity.project_findings(read.project, conditions)


def _structural(code: str, severity: str) -> findings_v2.StructuralFinding:
    return findings_v2.StructuralFinding(
        code=code,
        severity=severity,
        message=f"{code} here",
        location=MappingProxyType({"board_id": "DB1", "component": "c-1"}),
        blocks_execution=code in findings_v2.EXECUTION_BLOCKING,
    )


@pytest.mark.parametrize(
    ("code", "emitted", "severity", "confirm"),
    [
        # The findings module's severity is kept; confirmation is the policy's.
        ("template_unreviewed", "warning", FindingSeverity.WARNING, True),
        ("template_rejected", "blocking", FindingSeverity.BLOCKING, True),
        ("out_of_execution_scope", "blocking", FindingSeverity.BLOCKING, True),
        ("unconnected_required_terminal", "blocking", FindingSeverity.BLOCKING, False),
        ("wire_attribute_unset", "warning", FindingSeverity.WARNING, True),
        # A severity the store does not know is stored blocking.
        ("a_code_not_yet_mapped", "fatal", FindingSeverity.BLOCKING, False),
    ],
)
def test_v2_findings_are_stored_as_decided(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    emitted: str,
    severity: FindingSeverity,
    confirm: bool,
) -> None:
    monkeypatch.setattr(
        findings_v2, "structural_findings", lambda _model: (_structural(code, emitted),)
    )
    (found,) = design_projects._v2_findings(None)  # type: ignore[arg-type]
    assert found.code == code
    assert found.severity is severity
    assert found.requires_engineering_confirmation is confirm
    assert found.source == "integrity"
    assert found.board_id == "DB1"
    assert found.values == {"board_id": "DB1", "component": "c-1"}


def test_the_findings_module_grades_the_codes_as_decided() -> None:
    """Rejected or restricted templates block approval; unreviewed ones do not."""
    assert {
        "template_unreviewed",
        "template_rejected",
        "out_of_execution_scope",
    } == findings_v2.EXECUTION_BLOCKING


def test_execution_blockers_name_the_v2_execution_codes() -> None:
    found = [
        DesignFinding(
            code=code,
            severity=FindingSeverity.WARNING,
            message=code,
            requires_engineering_confirmation=True,
            source="integrity",
        )
        for code in ("out_of_execution_scope", "template_unreviewed", "wire_attribute_unset")
    ]
    checked = design_projects._Validation(ExportStatus.REVIEW_APPROVED, found, True)
    blockers = design_projects._execution_blockers(checked)
    assert "template_unreviewed" in blockers
    assert "out_of_execution_scope" in blockers
    assert "wire_attribute_unset" not in blockers
    assert "approval" not in blockers


def test_printed_findings_leave_out_the_v2_structural_ones() -> None:
    kept = DesignFinding(
        code="cable_overloaded", severity=FindingSeverity.BLOCKING, message="m", source="policy"
    )
    dropped = DesignFinding(
        code="template_unreviewed", severity=FindingSeverity.WARNING, message="m", source="x"
    )
    assert design_projects._printed([kept, dropped]) == [kept]


def test_the_written_schema_default_agrees_in_both_places() -> None:
    """The setting's default and the model's documented default never drift apart."""
    from app.core.config import Settings
    from app.design.model import versions

    assert Settings.model_fields["design_snapshot_schema"].default == versions.WRITTEN_BY_DEFAULT
