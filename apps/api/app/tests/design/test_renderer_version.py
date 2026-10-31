"""``RENDERER_VERSION`` changes whenever what an available export says changes.

An approval records the renderer version and counts only while it is
unchanged (``app.domain.design_projects``). That is only as good as the
version's upkeep, so this test renders a fixed synthetic project in every
available format and compares the content (``render_content``) with the
fingerprints recorded for the current version in
``renderer_fingerprints.json``:

* content changed, version not: the test fails. Bump ``RENDERER_VERSION``
  (approvals granted under the old one stop counting) and re-record;
* version changed: re-record, so the next change is caught.

Re-record with ``RECORD_RENDERER_FINGERPRINTS=1 pytest`` on this file.

It also shows the boundary: a wording change is caught, a change of how the
same words and drawing are styled is not.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from app import design
from app.design import export_dxf as export_dxf_module
from app.design import integrity, marking, render_pdf
from app.domain import design as design_domain
from app.models.schemas.design import (
    DistributionBoardRequest,
    ExportFormat,
    LoadInput,
    LoadKind,
    PricingSettings,
    ProjectDesignRequest,
    ProjectInfo,
)
from app.tests.design import render_content

RECORDED = Path(__file__).with_name("renderer_fingerprints.json")

#: The formats an approved revision can be exported in, and how each is read.
AVAILABLE: dict[ExportFormat, Callable[[bytes], object]] = {
    ExportFormat.PDF: render_content.pdf_content,
    ExportFormat.CALCULATIONS_PDF: render_content.pdf_content,
    ExportFormat.QUOTATION_PDF: render_content.pdf_content,
    ExportFormat.DXF: render_content.dxf_content,
    ExportFormat.JSON: render_content.json_content,
}

IDENTITY = "Reference, revision 01, approved by A. Reviewer on 2026-10-31, design 0123456789ab"


def _loads(*rows: tuple[str, LoadKind, str, dict[str, object]]) -> list[LoadInput]:
    return [
        LoadInput.model_validate({"description": d, "load": k, "power_kw": kw, **extra})
        for d, k, kw, extra in rows
    ]


#: The synthetic projects every format is rendered from: the content each
#: exercises (motor circuits and starters, right-to-left drawing languages,
#: findings sheets) is held to the recorded version too.
VARIANTS: dict[str, ProjectDesignRequest] = {
    "basic": ProjectDesignRequest(
        info=ProjectInfo(name="Renderer reference"),
        boards=[
            DistributionBoardRequest(
                name="DB1",
                loads=_loads(
                    ("Sockets", LoadKind.SOCKET, "3", {}),
                    ("Lighting", LoadKind.LIGHTING, "1.2", {}),
                ),
            )
        ],
    ),
    "motor": ProjectDesignRequest(
        info=ProjectInfo(name="Renderer motors"),
        boards=[
            DistributionBoardRequest(
                name="MCC1",
                loads=_loads(
                    ("Pump", LoadKind.MOTOR, "22", {"phases": 3, "starter": "star_delta"}),
                    ("Fan", LoadKind.MOTOR, "2.2", {"phases": 3, "starter": "dol"}),
                    ("Conveyor", LoadKind.MOTOR, "4", {"phases": 3, "starter": "drive"}),
                    ("Sockets", LoadKind.SOCKET, "2", {}),
                ),
            )
        ],
    ),
    "arabic": ProjectDesignRequest(
        info=ProjectInfo(name="لوحة مرجعية"),
        profile={"key": "synthetic-ar", "name": "شركة تجريبية", "language": "ar"},
        boards=[
            DistributionBoardRequest(
                name="DB1",
                loads=_loads(
                    ("مقابس", LoadKind.SOCKET, "3", {}),
                    ("إنارة", LoadKind.LIGHTING, "1.2", {}),
                    ("مضخة", LoadKind.MOTOR, "5.5", {"phases": 3, "starter": "dol"}),
                ),
            )
        ],
    ),
    "hebrew": ProjectDesignRequest(
        info=ProjectInfo(name="לוח ייחוס"),
        profile={"key": "synthetic-he", "name": "חברה לדוגמה", "language": "he"},
        boards=[
            DistributionBoardRequest(
                name="DB1",
                loads=_loads(
                    ("שקעים", LoadKind.SOCKET, "3", {}),
                    ("תאורה", LoadKind.LIGHTING, "1.2", {}),
                    ("משאבה", LoadKind.MOTOR, "5.5", {"phases": 3, "starter": "dol"}),
                ),
            )
        ],
    ),
}


def _contents(variant: str = "basic", label: str = marking.REVIEW_APPROVED_LABEL) -> dict[str, str]:
    request = VARIANTS[variant]
    designed, company = design_domain.build_design(request)
    conditions = {board.name: board.conditions for board in request.boards}
    # The real findings of the design, so the findings sheet is exercised.
    mark = marking.ExportMark(
        label=label,
        identity=IDENTITY,
        findings=tuple(integrity.project_findings(designed, conditions)),
    )
    pricing = PricingSettings.model_validate({"currency": "JOD", "labour_per_circuit": "10"})
    found: dict[str, str] = {}
    for export_format, read in AVAILABLE.items():
        exported = design_domain.render_export(
            designated=designed,
            company=company,
            export_format=export_format,
            pricing=pricing,
            mark=mark,
        )
        found[export_format.value] = render_content.fingerprint(read(exported.content))
    return found


def _all() -> dict[str, dict[str, str]]:
    return {variant: _contents(variant) for variant in VARIANTS}


def test_the_recorded_content_is_this_renderer_versions() -> None:
    found = _all()
    if os.environ.get("RECORD_RENDERER_FINGERPRINTS") == "1":
        RECORDED.write_text(
            json.dumps({"renderer_version": design.RENDERER_VERSION, "variants": found}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert recorded["renderer_version"] == design.RENDERER_VERSION, (
        "RENDERER_VERSION changed: re-record renderer_fingerprints.json "
        "(RECORD_RENDERER_FINGERPRINTS=1)"
    )
    changed = sorted(
        f"{variant} {name}"
        for variant, formats in found.items()
        for name, value in formats.items()
        if recorded["variants"].get(variant, {}).get(name) != value
    )
    assert not changed, (
        f"what {', '.join(changed)} exports say changed under the same RENDERER_VERSION: "
        "bump it in app/design/__init__.py (approvals granted under the old one stop "
        "counting), then re-record renderer_fingerprints.json"
    )


def test_every_variant_exercises_its_own_content() -> None:
    """No two variants render the same content in any format."""
    found = _all()
    for name in AVAILABLE:
        values = [found[variant][name.value] for variant in VARIANTS]
        assert len(set(values)) == len(values), name


def test_a_wording_change_is_material() -> None:
    """The same design with its approval label reworded: every format says so."""
    reworded = "REVIEW APPROVED — STUDY ONLY, NOT FOR EXECUTION"
    for variant in ("basic", "hebrew"):
        before, after = _contents(variant), _contents(variant, reworded)
        assert {name for name in before if before[name] != after[name]} == set(before)


def test_moved_drawing_geometry_is_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sheets placed elsewhere in the DXF: the same words, other coordinates."""
    before = _contents("motor")
    monkeypatch.setattr(export_dxf_module, "SHEET_GAP", export_dxf_module.SHEET_GAP + 5)
    after = _contents("motor")
    assert before["dxf"] != after["dxf"]
    assert {name for name in before if before[name] != after[name]} == {"dxf"}


def test_a_styling_change_is_not_material(monkeypatch: pytest.MonkeyPatch) -> None:
    before = _all()
    monkeypatch.setattr(render_pdf, "_DASH", (3.0, 2.0))
    monkeypatch.setattr(
        export_dxf_module, "_LAYERS", dict.fromkeys(export_dxf_module._LAYERS, 3)
    )
    assert _all() == before
