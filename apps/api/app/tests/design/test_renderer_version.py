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
from decimal import Decimal
from pathlib import Path

import pytest

from app import design
from app.design import export_dxf as export_dxf_module
from app.design import marking, render_pdf
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

MARK = marking.ExportMark(
    label=marking.REVIEW_APPROVED_LABEL,
    identity="Reference, revision 01, approved by A. Reviewer on 2026-10-31, design 0123456789ab",
    findings=(),
)


def _request() -> ProjectDesignRequest:
    return ProjectDesignRequest(
        info=ProjectInfo(name="Renderer reference"),
        boards=[
            DistributionBoardRequest(
                name="DB1",
                loads=[
                    LoadInput(description="Sockets", load=LoadKind.SOCKET, power_kw=Decimal("3")),
                    LoadInput(
                        description="Lighting", load=LoadKind.LIGHTING, power_kw=Decimal("1.2")
                    ),
                ],
            )
        ],
    )


def _contents(mark: marking.ExportMark = MARK) -> dict[str, str]:
    designed, company = design_domain.build_design(_request())
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


def test_the_recorded_content_is_this_renderer_versions() -> None:
    found = _contents()
    if os.environ.get("RECORD_RENDERER_FINGERPRINTS") == "1":
        RECORDED.write_text(
            json.dumps({"renderer_version": design.RENDERER_VERSION, "formats": found}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert recorded["renderer_version"] == design.RENDERER_VERSION, (
        "RENDERER_VERSION changed: re-record renderer_fingerprints.json "
        "(RECORD_RENDERER_FINGERPRINTS=1)"
    )
    changed = sorted(
        name for name, value in found.items() if recorded["formats"].get(name) != value
    )
    assert not changed, (
        f"what {', '.join(changed)} exports say changed under the same RENDERER_VERSION: "
        "bump it in app/design/__init__.py (approvals granted under the old one stop "
        "counting), then re-record renderer_fingerprints.json"
    )


def test_a_wording_change_is_material() -> None:
    """The same design with its approval label reworded: every format says so."""
    reworded = marking.ExportMark(
        label="REVIEW APPROVED — STUDY ONLY, NOT FOR EXECUTION",
        identity=MARK.identity,
        findings=(),
    )
    before, after = _contents(), _contents(reworded)
    assert {name for name in before if before[name] != after[name]} == set(before)


def test_a_styling_change_is_not_material(monkeypatch: pytest.MonkeyPatch) -> None:
    before = _contents()
    monkeypatch.setattr(render_pdf, "_DASH", (3.0, 2.0))
    monkeypatch.setattr(export_dxf_module, "_LAYERS", dict.fromkeys(export_dxf_module._LAYERS, 3))
    assert _contents() == before
