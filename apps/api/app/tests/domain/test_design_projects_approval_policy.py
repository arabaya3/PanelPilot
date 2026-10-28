"""Unit tests of the approval-policy binding in `app/domain/design_projects.py`.

No database: a revision row is built in memory, and what applies "now" is
an ``_Applicable`` given to the check. The Postgres-backed behaviour (approve,
open, export) is tested in ``test_design_projects.py`` and the route tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.design import CATALOGUE_VERSION, RULES_VERSION
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
    legacy = _row()
    assert _approval_changed(legacy, _now()) == ()
    assert _approval_changed(legacy, _now(findings=[("x", "info", False)])) == ("findings_policy",)
    assert _approval_changed(legacy, _now(rules_version="r2")) == ("rules_version",)


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
    assert _approval_changed(_row(), shallow) == ()


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
