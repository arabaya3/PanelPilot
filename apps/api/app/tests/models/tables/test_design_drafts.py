"""The draft tables' identity and integrity rules, as declared."""

from __future__ import annotations

from app.models.tables.design_drafts import DesignDraftRequestRow, DesignDraftRow


def _names(table: object) -> set[str]:
    return {c.name for c in table.constraints if c.name}  # type: ignore[attr-defined]


def test_one_draft_per_author_and_project_and_a_tombstone_has_no_body() -> None:
    names = _names(DesignDraftRow.__table__)
    assert "uq_design_drafts_author_project" in names
    assert {"state", "version", "body_when_live"} <= {
        n.removeprefix("ck_design_drafts_") for n in names
    }


def test_a_request_id_is_unique_per_author() -> None:
    assert "uq_design_draft_requests_request" in _names(DesignDraftRequestRow.__table__)
    assert "body" not in DesignDraftRequestRow.__table__.columns
