"""Unit tests of the approval-policy binding in `app/domain/design_projects.py`.

No database: a revision row is built in memory, and what applies "now" is
an ``_Applicable`` given to the check. The Postgres-backed behaviour (approve,
open, export) is tested in ``test_design_projects.py`` and the route tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.errors import ConflictError
from app.design import CATALOGUE_VERSION, RENDERER_BASELINE, RENDERER_VERSION, RULES_VERSION
from app.domain import design_projects
from app.domain.design_projects import _Applicable, _approval_changed, _findings_fingerprint
from app.models.tables.design_projects import DesignRevisionRow

PROFILE = "a" * 64
SNAPSHOT = "b" * 64
FINDINGS = [{"code": "x", "severity": "warning", "requires_engineering_confirmation": False}]
CLASSIFIED = [("x", "warning", False)]


def _row(**values: object) -> DesignRevisionRow:
    row = DesignRevisionRow(
        snapshot_sha256=SNAPSHOT,
        profile_sha256=PROFILE,
        rules_version=RULES_VERSION,
        catalogue_version=CATALOGUE_VERSION,
        findings=[
            {**found, "values": {}, "message": "m", "source": "policy"} for found in FINDINGS
        ],
        approved_at=datetime(2026, 10, 1, tzinfo=UTC),
        approved_snapshot_sha256=SNAPSHOT,
    )
    for name, value in values.items():
        setattr(row, name, value)
    return row


def _now(**values: object) -> _Applicable:
    base: dict[str, object] = {
        "thorough": True,
        "rules_version": RULES_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "profile_sha256": PROFILE,
        "findings": CLASSIFIED,
    }
    return _Applicable(**{**base, **values})  # type: ignore[arg-type]


def _recorded() -> dict[str, object]:
    return {
        "approved_profile_sha256": PROFILE,
        "approved_rules_version": RULES_VERSION,
        "approved_catalogue_version": CATALOGUE_VERSION,
        "approved_findings_policy": _findings_fingerprint(CLASSIFIED),
        "approved_renderer_version": RENDERER_VERSION,
    }


def test_an_approval_under_todays_policy_counts() -> None:
    assert _approval_changed(_row(**_recorded()), _now()) == ()


def test_no_approval_has_nothing_changed() -> None:
    assert _approval_changed(_row(approved_at=None), _now(rules_version="other")) == ()


def test_each_recorded_policy_value_is_compared_with_today() -> None:
    row = _row(**_recorded())
    assert _approval_changed(row, _now(rules_version="r2")) == ("rules_version",)
    assert _approval_changed(row, _now(catalogue_version="c2")) == ("catalogue_version",)
    assert _approval_changed(row, _now(profile_sha256="c" * 64)) == ("profile",)
    assert _approval_changed(row, _now(findings=[("x", "blocking", False)])) == ("findings_policy",)


def test_the_recorded_values_win_over_the_revisions_own() -> None:
    """An approval recorded under another policy does not count, whatever the row says."""
    row = _row(**{**_recorded(), "approved_rules_version": "r0", "approved_profile_sha256": "d"})
    assert _approval_changed(row, _now()) == ("rules_version", "profile")


def test_another_design_approved_is_named_snapshot() -> None:
    row = _row(**{**_recorded(), "approved_snapshot_sha256": "f" * 64})
    assert _approval_changed(row, _now()) == ("snapshot",)


def test_a_legacy_approval_reads_the_revisions_own_values() -> None:
    """Unrecorded values read as the revision's own; the findings as its stored ones."""
    legacy = _row(approved_renderer_version=RENDERER_VERSION)
    assert _approval_changed(legacy, _now()) == ()
    assert _approval_changed(legacy, _now(findings=[("x", "info", False)])) == ("findings_policy",)
    assert _approval_changed(legacy, _now(rules_version="r2")) == ("rules_version",)
    # With no renderer recorded either, it does not count (see the renderer tests).
    assert _approval_changed(_row(), _now()) == ("renderer_version",)


def test_a_listed_revision_is_checked_from_its_columns_alone() -> None:
    """No snapshot read: versions, the profile hash, and the recorded findings fingerprint."""
    row = _row(**_recorded())
    shallow = _Applicable(False, RULES_VERSION, CATALOGUE_VERSION, profile_sha256=PROFILE)
    assert _approval_changed(row, shallow) == ()
    assert _approval_changed(
        row, _Applicable(False, "r2", CATALOGUE_VERSION, profile_sha256=PROFILE)
    ) == ("rules_version",)
    other_profile = _row(**{**_recorded(), "approved_profile_sha256": "d"})
    assert _approval_changed(other_profile, shallow) == ("profile",)
    altered = _row(**{**_recorded(), "approved_findings_policy": "0" * 64})
    assert _approval_changed(altered, shallow) == ("findings_policy",)
    # A policy change since approval needs the snapshot: the cheap check misses it.
    assert _approval_changed(_row(approved_renderer_version=RENDERER_VERSION), shallow) == ()


def test_an_approval_of_a_snapshot_that_does_not_read_does_not_count() -> None:
    assert _approval_changed(_row(**_recorded()), _now(snapshot_reads=False)) == ("snapshot",)


def test_a_snapshot_that_does_not_read_leaves_the_findings_to_the_integrity_check() -> None:
    row = _row(**{**_recorded(), "approved_findings_policy": "0" * 64})
    assert _approval_changed(row, _now(findings=None)) == ()


def test_the_findings_fingerprint_is_canonical() -> None:
    first = _findings_fingerprint([("a", "warning", False), ("b", "info", True)])
    assert first == _findings_fingerprint(sorted([("b", "info", True), ("a", "warning", False)]))
    assert len(first) == 64
    assert first != _findings_fingerprint([("a", "blocking", False), ("b", "info", True)])


def test_the_frozen_name_is_the_approver() -> None:
    names = design_projects._Names(by_email={"e@x.com": "Live Name"}, by_id={})
    row = _row(approved_account="e@x.com", approved_display_name="Frozen Name")
    assert names.approver(row) == "Frozen Name"
    assert names.approver(_row(approved_account="e@x.com", approved_display_name="")) is None
    assert (
        names.printed_approver(_row(approved_account="e@x.com", approved_display_name=""))
        == design_projects.APPROVER_FALLBACK
    )
    # Only a row neither the approval nor the migration wrote is resolved live.
    assert names.approver(_row(approved_account="e@x.com")) == "Live Name"


# --- The renderer an approval was granted under


def test_a_renderer_change_since_approval_stops_it_counting() -> None:
    """A material change to what the exports print is not the same approval."""
    row = _row(**_recorded())
    assert _approval_changed(row, _now()) == ()
    assert _approval_changed(row, _now(renderer_version="r-next")) == ("renderer_version",)
    shallow = _Applicable(
        False, RULES_VERSION, CATALOGUE_VERSION, profile_sha256=PROFILE, renderer_version="r-next"
    )
    assert _approval_changed(row, shallow) == ("renderer_version",)


def test_an_approval_without_a_recorded_renderer_does_not_count() -> None:
    """Which renderer it was exported with is unknown: it is granted again."""
    row = _row(**{**_recorded(), "approved_renderer_version": None})
    assert row.approved_renderer_version is None
    assert RENDERER_BASELINE == "unversioned" != RENDERER_VERSION
    assert _approval_changed(row, _now()) == ("renderer_version",)


def test_a_renderer_change_leaves_an_unapproved_revision_alone() -> None:
    """The design is not the renderer's: a revision without an approval is not stale."""
    row = _row(approved_at=None)
    assert design_projects._changed(row, _now(renderer_version="r-next")) == ()
    assert _approval_changed(row, _now(renderer_version="r-next")) == ()


def test_the_stale_details_name_the_version_each_check_compared() -> None:
    """The approval's recorded rules version, not the revision's own twice."""
    row = _row(**{**_recorded(), "approved_rules_version": "r0"}, number=1)
    with pytest.raises(ConflictError) as approved_under_other:
        design_projects._refuse_stale(row, 1, _now())
    params = approved_under_other.value.params
    assert params["changed"] == "rules_version"
    assert params["recorded_rules_version"] == "r0"
    assert params["saved_rules_version"] == params["current_rules_version"] == RULES_VERSION
    with pytest.raises(ConflictError) as saved_under_other:
        design_projects._refuse_stale(_row(**_recorded(), rules_version="r-old"), 1, _now())
    assert saved_under_other.value.params["recorded_rules_version"] == "r-old"
    assert saved_under_other.value.params["current_rules_version"] == RULES_VERSION
