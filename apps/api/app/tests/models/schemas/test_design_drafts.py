"""Draft bodies are checked for their structure only, never engineering-validated."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.design_drafts import DRAFT_BODY_MAX_BYTES, DraftBody

_VALID = {
    "clean": "",
    "info": {"name": "N", "number": "", "customer": "", "consultant": "", "contractor": ""},
    "boards": [{"name": "DB1", "loads": [{"power": "not a number yet"}]}],
    "profileText": "",
    "language": None,
    "market": "",
}


def test_unfinished_work_is_accepted_as_typed() -> None:
    # A power that is not a number yet is unsaved work, not an engineering refusal.
    assert DraftBody.model_validate(_VALID).boards[0]["loads"][0]["power"] == "not a number yet"


@pytest.mark.parametrize(
    "change",
    [
        {"token": "x"},  # an unknown field (never a token or identity)
        {"boards": []},
        {"info": {"name": "N"}},
        {"profileText": "x" * 100_001},
    ],
)
def test_a_body_of_the_wrong_shape_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DraftBody.model_validate({**_VALID, **change})


def test_the_documented_limit() -> None:
    assert DRAFT_BODY_MAX_BYTES == 512 * 1024
