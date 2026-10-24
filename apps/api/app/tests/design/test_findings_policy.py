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
