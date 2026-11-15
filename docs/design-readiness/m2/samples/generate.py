"""Regenerate the M2 review sample exports and their summary (synthetic data only).

Run from ``apps/api`` with the API's environment (see the README here):

    python ../../docs/design-readiness/m2/samples/generate.py

Each sample is a configured ``pp-design/2`` design of a committed synthetic
case, rendered through the same functions a revision export uses
(``design_projects._printed``, ``schedule_pdf``, ``render_export``), as an
unapproved revision (the draft mark): no approval and no approver are shown.
``summary.json`` is then read back from the written files.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pdfplumber

from app.design import RULES_VERSION, marking, renderer_version, schedule_pdf, snapshot
from app.design.model import findings_v2, schedules, versions, write
from app.design.model.read import read_snapshot
from app.domain import design as design_domain
from app.domain import design_configuration, design_projects
from app.models.schemas.design import ExportFormat, ProjectDesignRequest
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import CONFIGS, config_case

HERE = Path(__file__).resolve().parent
CASES = ("pe_n_distribution", "io_marshalling")
RENDERER = renderer_version(versions.V2)


def _render(case: str) -> dict[str, bytes]:
    v1, configuration = config_case(case)
    stored = write.snapshot_v2_from_v1(v1, configuration)
    read = read_snapshot(stored)
    assert read.v2 is not None
    digest = snapshot.sha256_of(stored)
    request = ProjectDesignRequest.model_validate(DESIGNED[CONFIGS[case]["fixture"]]["request"])
    company = design_domain.resolve_profile(request.profile)
    conditions = {board.name: board.conditions for board in request.boards}
    findings = design_projects._design_findings(read.project, conditions, read.v2)
    name = f"Synthetic sample {case}"
    identity = (
        f"{name}, revision 02, not approved, design {digest[:design_projects.SHORT_HASH]}, "
        f"rules {RULES_VERSION}, renderer {RENDERER}"
    )
    mark = marking.ExportMark(
        label=marking.DRAFT_LABEL,
        identity=identity,
        findings=tuple(design_projects._printed(findings, versions.V2)),
    )
    header = schedule_pdf.ScheduleHeader(
        project=name,
        revision="02",
        design_sha256=digest,
        schema=versions.V2,
        rules_version=RULES_VERSION,
        renderer_version=RENDERER,
    )
    structural = [
        design_configuration.finding_view(f, design_projects._V2_CONFIRM)
        for f in findings_v2.structural_findings(read.v2)
    ]
    plain = marking.ExportMark(
        label=mark.label,
        identity=identity,
        findings=tuple(f for f in mark.findings if f.code not in findings_v2.CODES),
    )
    return {
        "terminal_schedule_pdf": schedule_pdf.render_terminal_schedule(
            schedules.terminal_schedule(read.v2), header, plain, structural
        ),
        "wire_schedule_pdf": schedule_pdf.render_wire_schedule(
            schedules.wire_schedule(read.v2), header, plain, structural
        ),
        "pdf": design_domain.render_export(
            designated=read.project,
            company=company,
            export_format=ExportFormat.PDF,
            pricing=None,
            mark=mark,
        ).content,
        "_sha256": digest.encode(),
    }


def _read_back(path: Path, digest: str) -> dict[str, object]:
    with pdfplumber.open(io.BytesIO(path.read_bytes())) as pdf:
        pages = [" ".join((p.extract_text() or "").split()) for p in pdf.pages]
    text = " ".join(pages)
    return {
        "file": path.name,
        "pages": len(pages),
        "labels_on_every_page": sorted(
            label
            for label in (marking.DRAFT_LABEL, schedule_pdf.SCHEDULE_LABEL)
            if all(label in page for page in pages)
        ),
        "not_for_execution_on_every_page": all("NOT FOR EXECUTION" in page for page in pages),
        "design_sha256": digest,
        "design_hash_printed": digest in text or digest[: design_projects.SHORT_HASH] in text,
        "renderer_printed": RENDERER in text,
        "findings_listed": sorted(code for code in findings_v2.CODES if code in text),
        # Value cells per source; a schedule's legend names each source once,
        # which is not counted.
        "source_cells": {
            source: text.count(source) - (1 if "Sources: [user]" in text else 0)
            for source in ("[user]", "[data]", "[assumption", "[unresolved]")
        },
    }


def main() -> None:
    """Write every sample, then ``summary.json`` read back from them."""
    summary: dict[str, object] = {
        "_comment": "SYNTHETIC SAMPLE DATA, read back from the files beside it by generate.py. "
        "Review content only; not for execution; no template or pattern in it is reviewed.",
        "schema": versions.V2,
        "renderer_version": RENDERER,
        "rules_version": RULES_VERSION,
        "samples": [],
    }
    for case in CASES:
        rendered = _render(case)
        digest = rendered.pop("_sha256").decode()
        for fmt, content in rendered.items():
            suffix = {"pdf": "drawing.pdf"}.get(fmt, fmt.replace("_pdf", ".pdf"))
            path = HERE / f"{case}.{suffix}"
            path.write_bytes(content)
            entry = {"case": case, "format": fmt, **_read_back(path, digest)}
            summary["samples"].append(entry)  # type: ignore[attr-defined]
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
