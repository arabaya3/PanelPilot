"""The approval-scope statement (M6d): what a review approval covers and does not."""

from __future__ import annotations

from app.design import approval_scope, marking


def test_the_statement_names_every_exclusion_and_says_it_is_not_validation() -> None:
    covers, not_covered = approval_scope.statement_lines()
    assert covers.startswith("[SCOPE] A review approval is a review of this revision's study")
    for part in approval_scope.NOT_COVERED:
        assert part in not_covered
    assert not_covered.endswith("It is not engineer validation.")


def test_every_mark_prints_the_scope_after_the_requirements() -> None:
    mark = marking.ExportMark(label="L", identity="I", requirements=("[OPEN] x",))
    lines = marking.finding_lines(mark)
    assert lines[-3:] == ["[OPEN] x", *approval_scope.statement_lines()]


def test_the_json_block_is_never_engineer_validated() -> None:
    block = approval_scope.block(
        approved=True,
        scope_version=approval_scope.APPROVAL_SCOPE_VERSION,
        excluded=["[EXCLUDED FROM SCOPE] 'fire pump'"],
        false_positives=[],
    )
    assert block["scope_confirmed"] is True
    assert block["engineer_validated"] is False
    assert (
        approval_scope.block(approved=True, scope_version=None, excluded=(), false_positives=())[
            "scope_confirmed"
        ]
        is False
    )
