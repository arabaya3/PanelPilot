"""Feeder links: entry checks, derivation, findings and printed rows (SYNTHETIC MB-1 to SB-1)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import ValidationError
from app.design.model import entities, links, write
from app.design.model.read import read_snapshot
from app.domain.design import build_design
from app.models.schemas.design import ProjectDesignRequest
from app.tests.design.model import m7a_support


def _model(configured: bool = False) -> entities.DesignModelV2:
    designed, _ = build_design(ProjectDesignRequest.model_validate(m7a_support.request()))
    model = read_snapshot(
        write.snapshot_v2_from_v1(
            designed.model_dump(mode="json"), m7a_support.configuration() if configured else None
        )
    ).v2
    assert model is not None
    return model


def _derive(entries: list[dict[str, Any]], configured: bool = False) -> Any:
    request = ProjectDesignRequest.model_validate(m7a_support.request(entries))
    links.check_request(request)
    model = _model(configured)
    return model, links.derive(
        model, request.feeder_links, {}, entered_by_user_id="u", entered_at="t"
    )


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ([{"fed_board": "X"}], "link_board_unknown"),
        ([{"fed_board": "MB-1"}], "link_not_a_feeder"),
        ([{"fed_board": "SB-1"}, {"fed_board": "SB-1"}], "link_duplicate"),
    ],
)
def test_entries_naming_no_feeder_are_refused(entries: list[dict[str, Any]], code: str) -> None:
    with pytest.raises(ValidationError) as raised:
        _derive(entries)
    assert raised.value.code == code
    assert raised.value.params["fed_board"] == entries[-1]["fed_board"]


def test_no_designer_value_is_copied_and_unset_prints_not_given() -> None:
    model, (link,) = _derive([])
    assert all(a.source == "unset" for _, a in link.attributes)
    assert link.provenance.entered_by_user_id is None
    (row,) = links.rows(model, [link])
    assert all(text == links.NOT_GIVEN for _, text in row.attributes)
    assert [e.terminal for e in row.ends] == [links.TERMINAL_NOT_SET] * 2
    codes = sorted(f.code for f in links.findings(model, [link]))
    assert codes == ["link_attribute_unset", "link_endpoint_unresolved", "link_endpoint_unresolved"]


def test_a_differing_value_shows_both_and_a_same_one_none() -> None:
    model, (link,) = _derive(
        [{"fed_board": "SB-1", "attributes": {"cores": 4, "material": " cu ", "length_m": "25"}}]
    )
    assert links.differences(model, link) == [("cores", "4", "5")]
    (found,) = [f for f in links.findings(model, [link]) if f.code.endswith("calculation")]
    assert found.location["user_value"] == "4"
    assert found.location["calculation_value"] == "5"


def test_terminals_are_checked_against_their_end_board() -> None:
    model = _model(configured=True)
    points = {s.board_id: s.points[0].id for s in model.strips}
    _, (link,) = _derive(
        [{"fed_board": "SB-1", "to_end": {"terminal": {"point": points["SB-1"], "side": "outer"}}}],
        configured=True,
    )
    assert link.ends[1].resolution == "resolved"
    for terminal, code in (
        ({"point": points["SB-1"], "side": "inner"}, "link_end_off_board"),
        ({"component": "c-none", "terminal": "x"}, "link_terminal_unknown"),
    ):
        with pytest.raises(ValidationError) as raised:
            _derive([{"fed_board": "SB-1", "from_end": {"terminal": terminal}}], configured=True)
        assert raised.value.code == code


def test_a_label_is_printed_as_user_text() -> None:
    model, (link,) = _derive([{"fed_board": "SB-1", "from_end": {"label": "Way 7"}}])
    (row,) = links.rows(model, [link])
    assert row.ends[0].label == f'"Way 7" ({links.LABEL_NOTE})'
    assert row.ends[0].resolved is False
