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

import dataclasses
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from reportlab.pdfgen.canvas import Canvas

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
        previous = json.loads(RECORDED.read_text(encoding="utf-8")) if RECORDED.exists() else {}
        # Recording never hides a change: new content needs a new version first.
        assert previous.get("renderer_version") != design.RENDERER_VERSION or (
            previous.get("variants") == found
        ), "the content changed: bump RENDERER_VERSION before re-recording"
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
    """The same design with its approval label reworded: every design format says so.

    A quotation never prints the design's label (it is a provisional
    estimate whatever the design's state), so it is the one format unchanged.
    """
    reworded = "REVIEW APPROVED — STUDY ONLY, NOT FOR EXECUTION"
    for variant in ("basic", "hebrew"):
        before, after = _contents(variant), _contents(variant, reworded)
        assert {name for name in before if before[name] != after[name]} == set(before) - {
            ExportFormat.QUOTATION_PDF.value
        }


def test_moved_drawing_geometry_is_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sheets placed elsewhere in the DXF: the same words, other coordinates."""
    before = _contents("motor")
    monkeypatch.setattr(export_dxf_module, "SHEET_GAP", export_dxf_module.SHEET_GAP + 5)
    after = _contents("motor")
    assert before["dxf"] != after["dxf"]
    assert {name for name in before if before[name] != after[name]} == {"dxf"}


def test_moved_or_resized_boxes_are_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Device boxes and frames drawn 7 mm further right: both drawing formats say so."""
    from app.design import pages, sheet

    original = sheet.Rect

    def shifted(*args: object, **kwargs: object) -> object:
        made = original(*args, **kwargs)  # type: ignore[arg-type]
        return dataclasses.replace(made, x=made.x + 7)

    before = _contents("motor")
    monkeypatch.setattr(pages, "Rect", shifted)
    after = _contents("motor")
    assert before["pdf"] != after["pdf"]
    assert before["dxf"] != after["dxf"]


def test_a_pdf_only_drawing_change_is_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines dropped, or dashing changed, in the PDF writer alone."""
    before = _contents("motor")
    monkeypatch.setattr(render_pdf, "_DASH", (3.0, 2.0))
    assert _contents("motor")["pdf"] != before["pdf"]
    monkeypatch.undo()
    monkeypatch.setattr(Canvas, "line", lambda *_args, **_kwargs: None)
    assert _contents("motor")["pdf"] != before["pdf"]


def test_a_demonstrably_cosmetic_change_is_not_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """DXF layer colours and the PDF's creator property: same content everywhere."""
    before = _all()
    monkeypatch.setattr(export_dxf_module, "_LAYERS", dict.fromkeys(export_dxf_module._LAYERS, 3))
    original = Canvas.setCreator
    monkeypatch.setattr(Canvas, "setCreator", lambda self, _value: original(self, "Other"))
    assert _all() == before


# --------------------------------------------------------------------------- #
# pp-design/2: its own renderer version, its own fingerprints
# --------------------------------------------------------------------------- #

RECORDED_V2 = Path(__file__).with_name("renderer_fingerprints_v2.json")

#: The configured synthetic cases a v2 revision's exports are rendered from:
#: the data-only PE/N distribution, the I/O marshalling strip, and the two
#: connection drawing cases with symbol data (M4), and the crowded routed case
#: (M5: crossings, continuations across sheets, unrouted connections).
V2_CASES = (
    "pe_n_distribution",
    "io_marshalling",
    "drawing_control_relay",
    "drawing_heldout_feeder",
    "drawing_routed_crowded",
)

#: What a v2 revision exports for review: the v1 formats (printing the v2
#: structural findings too), the two review-only schedules and the
#: review-only connection drawing (M4).
AVAILABLE_V2: dict[ExportFormat, Callable[[bytes], object]] = {
    **AVAILABLE,
    ExportFormat.TERMINAL_SCHEDULE_PDF: render_content.pdf_content,
    ExportFormat.WIRE_SCHEDULE_PDF: render_content.pdf_content,
    ExportFormat.CONNECTION_DRAWING_PDF: render_content.pdf_content,
}


def v2_exports(
    case: str,
    label: str = marking.REVIEW_APPROVED_LABEL,
    v2_edit: Callable[[Any], Any] | None = None,
) -> dict[str, bytes]:
    """Every review export of a configured v2 design, as ``export_revision`` makes it.

    The mark prints every finding (v1 checks and the structural ones), as a
    v2 revision's export does; the schedules come from the saved model alone.
    """
    from app.design import connection_drawing_pdf, schedule_pdf, snapshot
    from app.design.model import connection_drawing, findings_v2, schedules, versions, write
    from app.design.model.read import read_snapshot
    from app.domain import design_configuration, design_projects
    from app.tests.design.model.compat_support import DESIGNED
    from app.tests.design.model.v2_support import CONFIGS, config_case

    v1, configuration = config_case(case)
    stored = write.snapshot_v2_from_v1(v1, configuration)
    read = read_snapshot(stored)
    assert read.v2 is not None
    if v2_edit is not None:
        read = dataclasses.replace(read, v2=v2_edit(read.v2))
    v2 = read.v2
    assert v2 is not None
    request = ProjectDesignRequest.model_validate(DESIGNED[CONFIGS[case]["fixture"]]["request"])
    company = design_domain.resolve_profile(request.profile)
    conditions = {board.name: board.conditions for board in request.boards}
    findings = design_projects._design_findings(read.project, conditions, read.v2)
    mark = marking.ExportMark(
        label=label,
        identity=IDENTITY,
        findings=tuple(design_projects._printed(findings, versions.V2)),
    )
    pricing = PricingSettings.model_validate({"currency": "JOD", "labour_per_circuit": "10"})
    found: dict[str, bytes] = {}
    for export_format in AVAILABLE:
        found[export_format.value] = design_domain.render_export(
            designated=read.project,
            company=company,
            export_format=export_format,
            pricing=pricing,
            mark=mark,
        ).content
    header = schedule_pdf.ScheduleHeader(
        project="Renderer reference v2",
        revision="01",
        design_sha256=snapshot.sha256_of(stored),
        schema=versions.V2,
        rules_version=design.RULES_VERSION,
        renderer_version="(fixed for the fingerprint)",
    )
    structural = [
        design_configuration.finding_view(f, design_projects._V2_CONFIRM)
        for f in findings_v2.structural_findings(v2)
    ]
    plain = marking.ExportMark(
        label=mark.label,
        identity=mark.identity,
        findings=tuple(f for f in mark.findings if f.code not in findings_v2.CODES),
    )
    found[ExportFormat.TERMINAL_SCHEDULE_PDF.value] = schedule_pdf.render_terminal_schedule(
        schedules.terminal_schedule(v2), header, plain, structural
    )
    found[ExportFormat.WIRE_SCHEDULE_PDF.value] = schedule_pdf.render_wire_schedule(
        schedules.wire_schedule(v2), header, plain, structural
    )
    found[ExportFormat.CONNECTION_DRAWING_PDF.value] = (
        connection_drawing_pdf.render_connection_drawing(
            connection_drawing.drawing_set(v2),
            connection_drawing_pdf.DrawingHeader(**dataclasses.asdict(header)),
            plain,
            structural,
        )
    )
    return found


def _v2_contents(case: str, label: str = marking.REVIEW_APPROVED_LABEL) -> dict[str, str]:
    exported = v2_exports(case, label)
    return {
        name.value: render_content.fingerprint(read(exported[name.value]))
        for name, read in AVAILABLE_V2.items()
    }


def test_each_schema_has_its_own_renderer_version() -> None:
    """v1 keeps ``RENDERER_VERSION`` (no v1 approval moves); v2 has its own."""
    from app.design.model import versions

    assert set(design.RENDERER_VERSIONS) == set(versions.ORDER)
    assert design.RENDERER_VERSIONS[versions.V1] == design.RENDERER_VERSION
    assert design.renderer_version(versions.V2) != design.RENDERER_VERSION
    # Each fits the approval column (String(40)) and is never the baseline.
    for value in design.RENDERER_VERSIONS.values():
        assert len(value) <= 40
        assert value != design.RENDERER_BASELINE


def test_the_recorded_v2_content_is_the_v2_renderer_versions() -> None:
    from app.design.model import versions

    current = design.renderer_version(versions.V2)
    found = {case: _v2_contents(case) for case in V2_CASES}
    if os.environ.get("RECORD_RENDERER_FINGERPRINTS") == "1":
        previous = (
            json.loads(RECORDED_V2.read_text(encoding="utf-8")) if RECORDED_V2.exists() else {}
        )
        assert previous.get("renderer_version") != current or (
            previous.get("variants") == found
        ), "the v2 content changed: bump RENDERER_VERSIONS['pp-design/2'] before re-recording"
        RECORDED_V2.write_text(
            json.dumps({"renderer_version": current, "variants": found}, indent=2) + "\n",
            encoding="utf-8",
        )
    recorded = json.loads(RECORDED_V2.read_text(encoding="utf-8"))
    assert (
        recorded["renderer_version"] == current
    ), "RENDERER_VERSIONS['pp-design/2'] changed: re-record renderer_fingerprints_v2.json"
    changed = sorted(
        f"{case} {name}"
        for case, formats in found.items()
        for name, value in formats.items()
        if recorded["variants"].get(case, {}).get(name) != value
    )
    assert not changed, (
        f"what {', '.join(changed)} v2 exports say changed under the same v2 renderer "
        "version: bump RENDERER_VERSIONS['pp-design/2'] in app/design/__init__.py "
        "(approvals of v2 revisions granted under the old one stop counting), then re-record"
    )


def test_v2_variants_differ_and_a_v2_wording_change_is_material() -> None:
    found = {case: _v2_contents(case) for case in V2_CASES}
    for name in AVAILABLE_V2:
        assert found[V2_CASES[0]][name.value] != found[V2_CASES[1]][name.value], name
    reworded = _v2_contents(V2_CASES[0], "REVIEW APPROVED — STUDY ONLY, NOT FOR EXECUTION")
    assert {n for n in reworded if reworded[n] != found[V2_CASES[0]][n]} == set(reworded) - {
        ExportFormat.QUOTATION_PDF.value
    }


def test_v2_exports_print_the_structural_findings() -> None:
    """A v2 review document exposes its warnings and unresolved items; v1 has none."""
    import io

    import pdfplumber

    exported = v2_exports("pe_n_distribution")
    for name in (
        "pdf",
        "calculations_pdf",
        "terminal_schedule_pdf",
        "wire_schedule_pdf",
        "connection_drawing_pdf",
    ):
        with pdfplumber.open(io.BytesIO(exported[name])) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = " ".join(" ".join(pages).split())
        assert "template_unreviewed" in text, name
        assert "terminal_requiredness_unresolved" in text, name
        assert all("NOT FOR EXECUTION" in page for page in pages), name
    assert "template_unreviewed" in exported["json"].decode()


# --------------------------------------------------------------------------- #
# pp-design/3 (M7a): its own renderer version, its own fingerprints
# --------------------------------------------------------------------------- #

RECORDED_V3 = Path(__file__).with_name("renderer_fingerprints_v3.json")

#: The v3 variants, all of the SYNTHETIC two-board example (MB-1 feeds SB-1):
#: no entry at all (one unset, unresolved link), and a link with a resolved
#: end, a user label, attributes and two values differing from the
#: calculation, on the configured model.
V3_CASES = ("unentered", "entered")


def v3_exports(case: str, label: str = marking.REVIEW_APPROVED_LABEL) -> dict[str, bytes]:
    """Every review export of a v3 design, as ``export_revision`` makes it."""
    from app.design import connection_drawing_pdf, schedule_pdf, snapshot
    from app.design.model import codec_v3, connection_drawing, findings_v2, schedules, versions
    from app.design.model import links as link_model
    from app.design.model import write
    from app.design.model.read import read_snapshot
    from app.domain import design_configuration, design_projects
    from app.tests.design.model import m7a_support

    configured = case == "entered"
    request = ProjectDesignRequest.model_validate(m7a_support.request())
    designed, company = design_domain.build_design(request)
    v1 = designed.model_dump(mode="json")
    stored_v2 = write.snapshot_v2_from_v1(
        v1, m7a_support.configuration() if configured else None
    )
    model = read_snapshot(stored_v2).v2
    assert model is not None
    entered = None
    if configured:
        point = next(s for s in model.strips if s.board_id == "MB-1").points[0].id
        entered = ProjectDesignRequest.model_validate(
            m7a_support.request(
                [
                    {
                        "fed_board": "SB-1",
                        "from_end": {"terminal": {"point": point, "side": "inner"}},
                        "to_end": {"label": "Incomer top terminals"},
                        "attributes": {
                            "cores": 4,
                            "cross_section_mm2": "16",
                            "material": "Cu",
                            "length_m": "30",
                            "colour": "grey",
                        },
                    }
                ]
            )
        ).feeder_links
    links = link_model.derive(
        model,
        entered,
        {"MB-1": "b1", "SB-1": "b2"},
        entered_by_user_id="00000000-0000-4000-8000-000000000001" if configured else None,
        entered_at="2026-11-10T00:00:00+00:00" if configured else None,
    )
    stored = write.snapshot_v3_from_v2(stored_v2, links)
    read = read_snapshot(stored)
    v2 = read.v2
    assert v2 is not None
    conditions = {board.name: board.conditions for board in request.boards}
    findings = design_projects._design_findings(read.project, conditions, v2, read.links)
    mark = marking.ExportMark(
        label=label,
        identity=IDENTITY,
        findings=tuple(design_projects._printed(findings, versions.V3)),
    )
    pricing = PricingSettings.model_validate({"currency": "JOD", "labour_per_circuit": "10"})
    found: dict[str, bytes] = {}
    for export_format in AVAILABLE:
        found[export_format.value] = design_domain.render_export(
            designated=read.project,
            company=company,
            export_format=export_format,
            pricing=pricing,
            mark=mark,
            links=codec_v3.dump_links(read.links),
        ).content
    header = schedule_pdf.ScheduleHeader(
        project="Renderer reference v3",
        revision="01",
        design_sha256=snapshot.sha256_of(stored),
        schema=versions.V3,
        rules_version=design.RULES_VERSION,
        renderer_version="(fixed for the fingerprint)",
    )
    structural = [
        design_configuration.finding_view(f, design_projects._V2_CONFIRM)
        for f in findings_v2.structural_findings(v2)
    ]
    plain = marking.ExportMark(
        label=mark.label,
        identity=mark.identity,
        findings=tuple(f for f in mark.findings if f.code not in findings_v2.CODES),
    )
    rows = link_model.rows(v2, read.links)
    found[ExportFormat.TERMINAL_SCHEDULE_PDF.value] = schedule_pdf.render_terminal_schedule(
        schedules.terminal_schedule(v2), header, plain, structural
    )
    found[ExportFormat.WIRE_SCHEDULE_PDF.value] = schedule_pdf.render_wire_schedule(
        schedules.wire_schedule(v2), header, plain, structural, rows
    )
    found[ExportFormat.CONNECTION_DRAWING_PDF.value] = (
        connection_drawing_pdf.render_connection_drawing(
            connection_drawing.drawing_set(v2),
            connection_drawing_pdf.DrawingHeader(**dataclasses.asdict(header)),
            plain,
            structural,
            rows,
        )
    )
    return found


def _v3_contents(case: str, label: str = marking.REVIEW_APPROVED_LABEL) -> dict[str, str]:
    exported = v3_exports(case, label)
    return {
        name.value: render_content.fingerprint(read(exported[name.value]))
        for name, read in AVAILABLE_V2.items()
    }


def test_the_recorded_v3_content_is_the_v3_renderer_versions() -> None:
    from app.design.model import versions

    current = design.renderer_version(versions.V3)
    found = {case: _v3_contents(case) for case in V3_CASES}
    if os.environ.get("RECORD_RENDERER_FINGERPRINTS") == "1":
        previous = (
            json.loads(RECORDED_V3.read_text(encoding="utf-8")) if RECORDED_V3.exists() else {}
        )
        assert previous.get("renderer_version") != current or (
            previous.get("variants") == found
        ), "the v3 content changed: bump RENDERER_VERSIONS['pp-design/3'] before re-recording"
        RECORDED_V3.write_text(
            json.dumps({"renderer_version": current, "variants": found}, indent=2) + "\n",
            encoding="utf-8",
        )
    recorded = json.loads(RECORDED_V3.read_text(encoding="utf-8"))
    assert (
        recorded["renderer_version"] == current
    ), "RENDERER_VERSIONS['pp-design/3'] changed: re-record renderer_fingerprints_v3.json"
    changed = sorted(
        f"{case} {name}"
        for case, formats in found.items()
        for name, value in formats.items()
        if recorded["variants"].get(case, {}).get(name) != value
    )
    assert not changed, (
        f"what {', '.join(changed)} v3 exports say changed under the same v3 renderer "
        "version: bump RENDERER_VERSIONS['pp-design/3'] in app/design/__init__.py, then re-record"
    )


def test_v3_has_its_own_renderer_version_and_v1_v2_are_untouched() -> None:
    """M7a adds a v3 entry; the v1 and v2 entries and fingerprints are what they were."""
    from app.design.model import versions

    assert design.RENDERER_VERSIONS[versions.V1] == "2026.11.04.9"
    assert design.RENDERER_VERSIONS[versions.V2] == "2026.11.04.v2-24"
    assert design.renderer_version(versions.V3) not in (
        design.RENDERER_VERSIONS[versions.V1],
        design.RENDERER_VERSIONS[versions.V2],
    )


def test_v3_exports_print_the_links_and_differ_with_the_entry() -> None:
    import io

    import pdfplumber

    unentered, entered = _v3_contents("unentered"), _v3_contents("entered")
    for name in (
        ExportFormat.JSON,
        ExportFormat.WIRE_SCHEDULE_PDF,
        ExportFormat.CONNECTION_DRAWING_PDF,
    ):
        assert unentered[name.value] != entered[name.value], name
    exported = v3_exports("entered")
    for name in ("wire_schedule_pdf", "connection_drawing_pdf"):
        with pdfplumber.open(io.BytesIO(exported[name])) as pdf:
            text = " ".join(" ".join(page.extract_text() or "" for page in pdf.pages).split())
        assert "Board-to-board links" in text, name
        assert "link_value_differs_from_calculation" in text, name
    body = json.loads(exported["json"])
    assert [link["feeder"]["fed_board"] for link in body["links"]] == ["SB-1"]
