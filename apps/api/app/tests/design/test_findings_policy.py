"""Tests for `app/design/findings_policy.py`."""

from __future__ import annotations

from app.design import findings_policy, notes
from app.models.schemas.design import FindingSeverity

#: Codes made by the schedule import and suggestion, never on a board.
_NOT_BOARD_NOTES = {"split_points", "text"}


def test_every_board_note_code_is_classified() -> None:
    board_codes = {
        code
        for code in notes.TEMPLATES
        if not code.startswith("import_") and code not in _NOT_BOARD_NOTES
    }
    assert board_codes - set(findings_policy.POLICY) == set()
    assert set(findings_policy.POLICY) - set(notes.TEMPLATES) == set()


def test_an_unclassified_code_needs_an_engineer() -> None:
    found = findings_policy.classify("a_code_added_later")
    assert found.severity is FindingSeverity.WARNING
    assert found.requires_engineering_confirmation
    assert found.source == "policy"


def test_failed_sourced_checks_block_and_cite_their_rule() -> None:
    for code in ("earth_fault_exceeded", "short_circuit_min_exceeded", "voltage_drop_exceeded"):
        found = findings_policy.classify(code)
        assert found.severity is FindingSeverity.BLOCKING
        assert found.source != "policy"
    assert findings_policy.classify("mccb_needed").severity is FindingSeverity.BLOCKING
    assert findings_policy.classify("rules_unconfirmed").requires_engineering_confirmation
    assert findings_policy.classify("spare_ways").severity is FindingSeverity.INFO


def test_every_integrity_code_is_classified_and_none_is_left_over() -> None:
    import re
    from pathlib import Path

    from app.design import integrity

    source = Path(integrity.__file__).read_text(encoding="utf-8")
    made = set(re.findall(r'"((?:integrity|draft)_[a-z_]+)"', source))
    assert made == set(findings_policy.INTEGRITY)
    # Integrity codes are not note codes: the two tables never share a code.
    assert not set(findings_policy.INTEGRITY) & set(findings_policy.POLICY)


def test_what_the_integrity_check_leaves_out_or_breaks_is_blocking() -> None:
    for code in (
        "integrity_cable_missing",
        "integrity_protection_missing",
        "integrity_device_unrated",
        "integrity_device_above_cable_capacity",
    ):
        assert findings_policy.INTEGRITY[code].severity is FindingSeverity.BLOCKING, code
    for code in ("integrity_drive_cable_unchecked", "draft_findings_not_rechecked"):
        found = findings_policy.INTEGRITY[code]
        assert found.severity is FindingSeverity.WARNING, code
        assert found.requires_engineering_confirmation, code
    assert findings_policy.INTEGRITY["integrity_conditions_unknown"].severity is (
        FindingSeverity.INFO
    )
