"""``RULES_VERSION`` changes whenever what the checks find changes.

A revision whose stored findings are other finding codes than the checks
find on its stored design, under the rules and catalogue it records, is
refused as an integrity failure (``_findings_mismatch`` in
``app.domain.design_projects``): while the version is unchanged, the same
design must give the same findings. That holds only if every change to what
the checks find (``integrity.project_findings`` and the v2 structural
findings) bumps ``RULES_VERSION``; otherwise every intact saved revision the
change affects would read as altered.

So this test runs the current checks on every committed design fixture
(``model/fixtures``, as v1 and migrated to v2) and every committed v2
configuration (``model/configurations``), and compares the sorted multiset
of finding codes of each with ``findings_fingerprints.json``, recorded for
the current ``RULES_VERSION`` and ``CATALOGUE_VERSION``:

* codes changed, versions not: the test fails. Bump ``RULES_VERSION`` (saved
  revisions designed under the old one become stale, not altered) and
  re-record;
* version changed: re-record, so the next change is caught.

Re-record with ``RECORD_FINDINGS_FINGERPRINTS=1 pytest`` on this file; it
refuses to record new codes under an unchanged version.

Only the codes are held: a reclassification of the same codes is a
findings-policy change, which ``_changed`` reports as ``stale`` by itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app import design
from app.design.model import write
from app.design.model.read import read_snapshot
from app.domain import design_projects
from app.models.schemas.design import ProjectDesignRequest
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import CONFIGS, config_case, v1_snapshot

RECORDED = Path(__file__).with_name("findings_fingerprints.json")


def _codes(snapshot: dict[str, Any], request: dict[str, Any]) -> list[str]:
    """The sorted finding codes the current checks give a stored design."""
    read = read_snapshot(snapshot)
    parsed = ProjectDesignRequest.model_validate(request)
    conditions = {board.name: board.conditions for board in parsed.boards}
    found = design_projects._design_findings(read.project, conditions, read.v2)
    return sorted(item.code for item in found)


def _digest(codes: list[str]) -> str:
    return hashlib.sha256(json.dumps(codes).encode("utf-8")).hexdigest()


def _all() -> dict[str, str]:
    found: dict[str, str] = {}
    for case, body in sorted(DESIGNED.items()):
        found[f"v1 {case}"] = _digest(_codes(v1_snapshot(case), body["request"]))
        migrated = write.snapshot_v2_from_v1(v1_snapshot(case))
        found[f"v2 {case}"] = _digest(_codes(migrated, body["request"]))
    for name in sorted(CONFIGS):
        v1, configuration = config_case(name)
        request = DESIGNED[CONFIGS[name]["fixture"]]["request"]
        composed = write.snapshot_v2_from_v1(v1, configuration)
        found[f"configuration {name}"] = _digest(_codes(composed, request))
    return found


def _versions() -> dict[str, str]:
    return {
        "rules_version": design.RULES_VERSION,
        "catalogue_version": design.CATALOGUE_VERSION,
    }


def test_every_fixture_and_configuration_is_held() -> None:
    found = _all()
    assert len(found) == 2 * len(DESIGNED) + len(CONFIGS)


def test_the_recorded_findings_are_this_rules_versions() -> None:
    found = _all()
    if os.environ.get("RECORD_FINDINGS_FINGERPRINTS") == "1":
        previous = json.loads(RECORDED.read_text(encoding="utf-8")) if RECORDED.exists() else {}
        # Recording never hides a change: new findings need a new version first.
        assert previous.get("versions") != _versions() or previous.get("cases") == found, (
            "the findings changed: bump RULES_VERSION in app/design/__init__.py "
            "before re-recording"
        )
        RECORDED.write_text(
            json.dumps({"versions": _versions(), "cases": found}, indent=2) + "\n",
            encoding="utf-8",
        )
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert recorded["versions"] == _versions(), (
        "RULES_VERSION or CATALOGUE_VERSION changed: re-record findings_fingerprints.json "
        "(RECORD_FINDINGS_FINGERPRINTS=1)"
    )
    changed = sorted(case for case, value in found.items() if recorded["cases"].get(case) != value)
    assert not changed, (
        f"the findings of {', '.join(changed)} changed under the same RULES_VERSION: bump it "
        "in app/design/__init__.py, then re-record findings_fingerprints.json. Otherwise "
        "every intact saved revision this affects is refused as an integrity failure "
        "(findings_mismatch), since its stored findings no longer match its design."
    )


def test_a_changed_check_is_caught(monkeypatch: Any) -> None:
    """A check that finds one more code under the same version fails the comparison."""
    from app.design import integrity
    from app.models.schemas.design import DesignFinding, FindingSeverity

    real = integrity.project_findings

    def more(*args: Any, **kwargs: Any) -> list[DesignFinding]:
        extra = DesignFinding(
            code="a_new_check",
            severity=FindingSeverity.WARNING,
            values={},
            message="new",
            requires_engineering_confirmation=False,
            source="integrity",
        )
        return [*real(*args, **kwargs), extra]

    monkeypatch.setattr(integrity, "project_findings", more)
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert all(recorded["cases"][case] != value for case, value in _all().items())
