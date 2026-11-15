"""Safety concerns of a revision (M6e): the request itself, earlier revisions, the rules."""

from __future__ import annotations

from typing import Any

from app.design import safety_review as sr


def _request(*loads: str, name: str = "Tower") -> dict[str, Any]:
    return {
        "info": {"name": name},
        "boards": [{"name": "MDB", "loads": [{"description": d} for d in loads]}],
    }


def test_the_request_is_read_whatever_the_intake() -> None:
    found = sr.request_concerns(_request("Fire pump FP-1", "Standby pump"), 1)
    assert [(c.code, c.label, c.place) for c in found] == [
        ("request_life_safety", "fire pump", "load"),
        ("request_possibly_life_safety", "standby", "load"),
    ]
    assert found[0].where == "MDB \u203a Fire pump FP-1"
    assert found[0].load_index == 0
    assert sr.request_concerns(_request("Lighting", name="Fire Station Road"), 1)[0].place == (
        "info.name"
    )
    assert sr.request_concerns(None, 1) == []


def test_an_earlier_revisions_concern_is_carried_and_dated() -> None:
    earlier = sr.Stored(1, _request("Fire pump FP-1"), None)
    current = sr.Stored(2, _request("Booster pump"), None)
    (carried,) = sr.concerns(current, [earlier])
    assert carried.present is False
    assert carried.raised_on == 1
    # Still in the request: present, dated by the first revision.
    (still,) = sr.concerns(sr.Stored(2, _request("Fire pump FP-1"), None), [earlier])
    assert still.present is True
    assert still.raised_on == 1


def test_what_each_resolution_may_resolve() -> None:
    (life,) = sr.request_concerns(_request("Fire pump"), 1)
    (maybe,) = sr.request_concerns(_request("Standby pump"), 1)
    assert sr.refusal(life, sr.EXCLUDED_FROM_SCOPE) == "in_request"
    assert sr.refusal(life, sr.FALSE_POSITIVE) == "life_safety"
    assert sr.refusal(maybe, sr.EXCLUDED_FROM_SCOPE) == "in_request"
    assert sr.refusal(maybe, sr.FALSE_POSITIVE) is None
    (gone,) = sr.concerns(
        sr.Stored(2, _request(), None), [sr.Stored(1, _request("Fire pump"), None)]
    )
    assert sr.refusal(gone, sr.EXCLUDED_FROM_SCOPE) is None
    # Renamed, not removed: the same row is still drawn.
    (renamed,) = sr.concerns(
        sr.Stored(2, _request("FP-1"), None), [sr.Stored(1, _request("Fire pump"), None)]
    )
    assert sr.refusal(renamed, sr.EXCLUDED_FROM_SCOPE) == "still_in_request"


def test_lines_name_the_revision_and_are_never_validated() -> None:
    (gone,) = sr.concerns(
        sr.Stored(2, _request(), None), [sr.Stored(1, _request("Fire pump"), None)]
    )
    (line,) = sr.open_lines([gone], None)
    assert line.startswith("[OPEN] Safety concern (raised on revision 1): 'fire pump'")
    record = {
        "safety_resolutions": [
            {
                "target_kind": "request",
                "target_id": gone.target_id,
                "proposal_id": None,
                "resolution": "excluded_from_scope",
                "reason": "By the fire-protection contractor.",
                "by_user_id": "u",
                "by_name": "R. Reviewer",
                "at": "2026-11-04T10:00:00",
            }
        ]
    }
    assert sr.unresolved([gone], record) == []
    assert sr.open_lines([gone], record) == []
    (printed,) = sr.resolution_lines([gone], record)
    assert printed == (
        "[EXCLUDED FROM SCOPE] 'fire pump' (raised on revision 1) — not designed or reviewed "
        "here: By the fire-protection contractor. (recorded by R. Reviewer on 2026-11-04) "
        "— not engineer-validated"
    )
    assert sr.carried_resolutions(record) == record["safety_resolutions"]
