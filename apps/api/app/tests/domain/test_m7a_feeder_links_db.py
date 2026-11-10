"""M7a integration: feeder links between two boards in ``pp-design/3`` revisions.

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with a SYNTHETIC two-board example, MB-1 feeding SB-1
(``app.tests.design.model.m7a_support``): its names, markings and values are
illustrative only. Each test sets ``DESIGN_SNAPSHOT_SCHEMA`` itself, so the
module passes whatever the ambient switch is.

The invariants:

* entry, save and reopen give one hash; the link is derived from ``fed_from``
  and holds only what the user entered, provenance set by the server;
* the wire schedule and the connection drawing print the same link values
  (terminal, "terminal NOT SET", the user's label as user text, "not given");
* every refusal of the contract (unknown board, not a feeder, duplicate,
  terminal off its board, unknown terminal, client provenance, not v3);
* a value differing from the calculation shows both and changes nothing;
* tenant isolation, snapshot integrity and approval binding cover the links;
* v1 and v2 revisions open and export byte for byte as before under a v3
  deployment, their approval unchanged;
* execution and restricted formats stay refused; a life-safety word in a
  link's text holds the approval.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app import design
from app.core.config import get_settings
from app.design import snapshot
from app.design.model import codec_v3
from app.design.model.read import read_snapshot
from app.design.model.write import snapshot_v2_from_v1
from app.domain.design import build_design
from app.models.schemas.design import ProjectDesignRequest
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    REQUEST,
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    error,
    pdf_pages,
    pytestmark,
    revision_export,
    revision_row,
    save,
    update_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support

Switch = Callable[[str], None]


@pytest.fixture(name="switch")
def _switch(monkeypatch: pytest.MonkeyPatch) -> Iterator[Switch]:
    """Set ``DESIGN_SNAPSHOT_SCHEMA`` as a deployment would, through the settings."""

    def set_to(schema: str) -> None:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", schema)
        get_settings.cache_clear()

    yield set_to
    get_settings.cache_clear()


def _opened(client: TestClient, project_id: str, number: int = 1) -> dict[str, Any]:
    response = client.get(f"/design/projects/{project_id}", params={"revision": number})
    assert response.status_code == 200, response.text[:500]
    body: dict[str, Any] = response.json()
    return body


def _post(
    client: TestClient,
    links: list[dict[str, Any]] | None,
    configuration: dict[str, Any] | None = None,
) -> Any:
    """Save the synthetic project as revision 1, with a configuration when given."""
    # With a configuration, revision 1 is saved plainly and revision 2 carries
    # the configuration and the links (a terminal exists only in a configured model).
    body: dict[str, Any] = {
        "name": "M7a synthetic",
        "request": m7a_support.request(links if configuration is None else None),
        "note": "r1",
    }
    response = client.post("/design/projects", json=body)
    if configuration is None or response.status_code != 201:
        return response
    project_id = response.json()["id"]
    return client.post(
        f"/design/projects/{project_id}/revisions",
        json={
            "request": m7a_support.request(links),
            "note": "configured",
            "configuration": configuration,
        },
    )


def _saved(client: TestClient, links: list[dict[str, Any]] | None) -> str:
    response = _post(client, links)
    assert response.status_code == 201, response.text[:500]
    project_id: str = response.json()["id"]
    return project_id


def _text(content: bytes) -> str:
    return " ".join(" ".join(pdf_pages(content)).split())


def _cells(content: bytes) -> str:
    """Every table cell's text of a PDF, whitespace removed, joined (cells wrap lines)."""
    import io

    import pdfplumber

    out: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as document:
        for page in document.pages:
            for table in page.extract_tables():
                for row in table:
                    out += ["".join((cell or "").split()) for cell in row]
    return "|".join(out)


def _squeezed(text: str) -> str:
    return "".join(text.split())


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisions here are saved by another member, so the caller may approve them (M6a)."""
    review_support.author_revisions_as_another_member(monkeypatch)


def _points() -> dict[str, str]:
    """The strip point ids the synthetic configuration gives each board (deterministic)."""
    designed, _ = build_design(ProjectDesignRequest.model_validate(m7a_support.request()))
    model = read_snapshot(
        snapshot_v2_from_v1(designed.model_dump(mode="json"), m7a_support.configuration())
    ).v2
    assert model is not None
    return {strip.board_id: strip.points[0].id for strip in model.strips}


LINK = {
    "fed_board": "SB-1",
    "from_end": {"label": "Outgoing way 7"},
    "attributes": {"cores": 4, "cross_section_mm2": "16", "material": "Cu", "length_m": "30"},
}


# --------------------------------------------------------------------------- #
# Entry, save, reopen
# --------------------------------------------------------------------------- #


def test_a_v3_save_derives_the_link_and_reopens_with_the_same_hash(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    row = revision_row(engine, project_id, 1)
    stored = row["design_snapshot"]
    assert stored["schema"] == "pp-design/3"
    assert snapshot.sha256_of(stored) == row["snapshot_sha256"]
    (link,) = stored["model"]["links"]
    assert link["origin"] == "derived"
    assert link["feeder"] == {"supply_board": "MB-1", "fed_board": "SB-1", "circuit_id": "c2"}
    assert link["id"] == codec_v3.link_id("MB-1", "SB-1")
    assert [e["role"] for e in link["ends"]] == ["from", "to"]
    assert link["ends"][0] == {
        "role": "from",
        "board": "MB-1",
        "terminal": None,
        "label": "Outgoing way 7",
        "resolution": "unresolved",
    }
    assert link["attributes"]["cores"] == {"value": 4, "source": "user"}
    # Unset stays unset: no designer value is copied in.
    for name in ("insulation", "number", "colour"):
        assert link["attributes"][name] == {"value": None, "source": "unset"}
    provenance = link["provenance"]
    assert provenance["requirement_ref"].endswith(".feeder")
    assert provenance["entered_by_user_id"] == str(row["author_user_id"])
    assert provenance["entered_at"] is not None
    # The request keeps what was entered; the record has no open item for it.
    assert row["request"]["feeder_links"][0]["fed_board"] == "SB-1"
    codes = {item["code"] for item in row["requirements"]["open_items"]}
    assert "inter_board_wiring_not_modelled" not in codes
    first = _opened(client, project_id)
    second = _opened(client, project_id)
    assert first["snapshot_sha256"] == second["snapshot_sha256"] == row["snapshot_sha256"]
    assert first["snapshot_schema"] == "pp-design/3"
    assert first["export_status"] == "draft"
    assert first["links"] == stored["model"]["links"]
    assert first["request"]["feeder_links"][0]["from_end"]["label"] == "Outgoing way 7"
    found = {f["code"] for f in first["findings"]}
    assert {"link_endpoint_unresolved", "link_attribute_unset"} <= found


def test_a_v3_save_without_entries_still_derives_one_unset_link(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, None)
    row = revision_row(engine, project_id, 1)
    (link,) = row["design_snapshot"]["model"]["links"]
    assert all(a["source"] == "unset" for a in link["attributes"].values())
    assert link["provenance"]["entered_by_user_id"] is None
    assert link["provenance"]["entered_at"] is None
    assert "feeder_links" not in row["request"]


def test_v2_and_v1_saves_keep_the_open_item_and_store_no_links(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    for schema in ("pp-design/1", "pp-design/2"):
        switch(schema)
        row = revision_row(engine, _saved(client, None), 1)
        assert "links" not in json.dumps(row["design_snapshot"]).replace('"links": []', "")
        codes = {item["code"] for item in row["requirements"]["open_items"]}
        assert "inter_board_wiring_not_modelled" in codes
        assert "feeder_links" not in row["request"]


# --------------------------------------------------------------------------- #
# Output: the schedule and the drawing print the same values
# --------------------------------------------------------------------------- #


def test_schedule_drawing_and_json_show_the_same_link_values(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    points = _points()
    links = [
        {
            "fed_board": "SB-1",
            "from_end": {"terminal": {"point": points["MB-1"], "side": "inner"}},
            "to_end": {"label": "SB-1 incomer top"},
            "attributes": {"cores": 5, "colour": "grey"},
        }
    ]
    revised = _post(client, links, m7a_support.configuration())
    assert revised.status_code == 201, revised.text[:500]
    project_id = revised.json()["id"]
    row = revision_row(engine, project_id, 2)
    (link,) = row["design_snapshot"]["model"]["links"]
    assert link["ends"][0]["resolution"] == "resolved"
    assert link["ends"][1]["resolution"] == "unresolved"
    schedule = revision_export(client, project_id, 2, "wire_schedule_pdf")
    drawing = revision_export(client, project_id, 2, "connection_drawing_pdf")
    exported = revision_export(client, project_id, 2, "json")
    for response in (schedule, drawing, exported):
        assert response.status_code == 200, response.text[:300]
    s_text, d_text = _text(schedule.content), _text(drawing.content)
    cells, drawn = _cells(schedule.content), _squeezed(d_text)
    # One set of printed values, from one set of rows (``links.rows``).
    for value in (
        "XF:F1 (inner)",
        "terminal NOT SET",
        '"SB-1 incomer top" (label given by user — not a modelled terminal)',
        "cores: 5 [user]",
        "colour: grey [user]",
        "insulation: not given",
        "length m: not given",
    ):
        assert _squeezed(value) in cells, value
        assert _squeezed(value) in drawn, value
    for text in (s_text, d_text):
        assert f"renderer {design.renderer_version('pp-design/3')}" in text
        assert "Board-to-board links" in text
    body = json.loads(exported.content)
    assert body["links"] == row["design_snapshot"]["model"]["links"]
    assert "link_endpoint_unresolved" in {f["code"] for f in body["findings"]}


def test_an_unresolved_end_is_never_drawn_as_a_terminal(
    client: TestClient, switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    drawing = revision_export(client, project_id, 1, "connection_drawing_pdf")
    assert drawing.status_code == 200, drawing.text[:300]
    text = _text(drawing.content)
    assert "MB-1 terminal NOT SET" in text
    assert "SB-1 terminal NOT SET" in text
    assert "terminal drawn at" not in text


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("links", "code"),
    [
        ([{"fed_board": "NOWHERE"}], "link_board_unknown"),
        ([{"fed_board": "MB-1"}], "link_not_a_feeder"),
        ([{"fed_board": "SB-1"}, {"fed_board": "SB-1"}], "link_duplicate"),
    ],
)
def test_links_naming_no_feeder_or_one_twice_are_refused(
    client: TestClient, switch: Switch, links: list[dict[str, Any]], code: str
) -> None:
    switch("pp-design/3")
    refused = _post(client, links)
    assert refused.status_code == 422, refused.text[:300]
    assert error(refused)["code"] == code


def test_terminals_off_their_board_or_unknown_are_refused(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    points = _points()
    off_board = [
        {"fed_board": "SB-1", "from_end": {"terminal": {"point": points["SB-1"], "side": "inner"}}}
    ]
    refused = _post(client, off_board, m7a_support.configuration())
    assert refused.status_code == 422, refused.text[:300]
    assert error(refused)["code"] == "link_end_off_board"
    unknown = [
        {"fed_board": "SB-1", "to_end": {"terminal": {"component": "c-none", "terminal": "x"}}}
    ]
    refused = _post(client, unknown, m7a_support.configuration())
    assert refused.status_code == 422, refused.text[:300]
    assert error(refused)["code"] == "link_terminal_unknown"
    # Without a configuration no terminal is modelled at all.
    refused = _post(client, off_board)
    assert refused.status_code == 422
    assert error(refused)["code"] == "link_terminal_unknown"


def test_client_provenance_is_refused(client: TestClient, switch: Switch) -> None:
    switch("pp-design/3")
    refused = _post(client, [{**LINK, "provenance": {"entered_by_user_id": "someone"}}])
    assert refused.status_code == 422, refused.text[:300]


@pytest.mark.parametrize("schema", ["pp-design/1", "pp-design/2"])
def test_links_are_refused_where_revisions_are_not_v3(
    client: TestClient, switch: Switch, schema: str
) -> None:
    switch(schema)
    refused = _post(client, [LINK])
    assert refused.status_code == 409, refused.text[:300]
    body = error(refused)
    assert body["code"] == "feeder_links_require_v3"
    assert body["params"]["written_schema"] == schema


@pytest.mark.parametrize(
    "attributes",
    [{"cores": 0}, {"cores": 101}, {"cross_section_mm2": "0"}, {"length_m": "-1"}, {"colour": " "}],
)
def test_attribute_values_are_checked_by_type_and_range(
    client: TestClient, switch: Switch, attributes: dict[str, Any]
) -> None:
    switch("pp-design/3")
    refused = _post(client, [{"fed_board": "SB-1", "attributes": attributes}])
    assert refused.status_code == 422, refused.text[:300]


# --------------------------------------------------------------------------- #
# A user value that differs from the calculation
# --------------------------------------------------------------------------- #


def test_a_differing_value_shows_both_and_changes_no_calculation(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    plain = revision_row(engine, _saved(client, None), 1)
    linked_id = _saved(client, [LINK])
    linked = revision_row(engine, linked_id, 1)
    # The design itself, every calculated value included, is unchanged.
    assert linked["design_snapshot"]["model"]["design"] == plain["design_snapshot"]["model"]["design"]
    differing = [f for f in linked["findings"] if f["code"] == "link_value_differs_from_calculation"]
    by_attribute = {f["values"]["attribute"]: f for f in differing}
    assert set(by_attribute) == {"cores", "length_m"}
    cores = by_attribute["cores"]
    assert cores["severity"] == "warning"
    assert cores["values"]["user_value"] == "4"
    assert cores["values"]["calculation_value"] == "5"
    assert cores["values"]["user_source"] == "user"
    assert cores["values"]["calculation_source"] == "feeder calculation"
    assert "4" in cores["message"] and "5" in cores["message"]
    assert "wrong" not in cores["message"].lower() and "correct" not in cores["message"].lower()
    # It does not block approval by itself.
    assert approve(client, linked_id, 1).status_code == 200
    schedule = revision_export(client, linked_id, 1, "wire_schedule_pdf")
    assert _squeezed("cores: 4 [user] / 5 [feeder calculation]") in _cells(schedule.content)


# --------------------------------------------------------------------------- #
# Isolation and integrity
# --------------------------------------------------------------------------- #


def test_another_tenant_cannot_see_a_v3_revision(
    client: TestClient, caller: _Caller, tenants: tuple[str, str], switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert client.get(f"/design/projects/{project_id}").status_code == 404
    assert revision_export(client, project_id, 1, "json").status_code == 404
    assert revision_export(client, project_id, 1, "wire_schedule_pdf").status_code == 404
    assert approve(client, project_id, 1).status_code == 404


def _tamper(stored: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(stored)
    out["model"]["links"][0]["ends"][0]["label"] = "Changed outside PanelPilot"
    return out


def test_a_tampered_link_fails_integrity(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    stored = revision_row(engine, project_id, 1)["design_snapshot"]
    update_row(engine, project_id, 1, design_snapshot=_tamper(stored))
    opened = _opened(client, project_id)
    assert opened["export_status"] == "not_validated"
    assert "snapshot_integrity_failed" in {f["code"] for f in opened["findings"]}
    assert opened["links"] is None
    refused = revision_export(client, project_id, 1, "json")
    assert refused.status_code == 409
    assert error(refused)["code"] == "snapshot_integrity_failed"
    refused = approve(client, project_id, 1)
    assert error(refused)["code"] == "snapshot_integrity_failed"


def test_a_rehashed_link_tamper_after_approval_is_not_the_approved_design(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    assert approve(client, project_id, 1).status_code == 200
    assert _opened(client, project_id)["approval_current"] is True
    stored = _tamper(revision_row(engine, project_id, 1)["design_snapshot"])
    update_row(
        engine, project_id, 1, design_snapshot=stored, snapshot_sha256=snapshot.sha256_of(stored)
    )
    opened = _opened(client, project_id)
    assert opened["approval_current"] is False
    assert "snapshot" in opened["approval_changed"]
    exported = revision_export(client, project_id, 1, "pdf")
    if exported.status_code == 200:
        assert "REVIEW APPROVED" not in _text(exported.content)


def test_a_link_that_is_not_the_feeders_derived_one_is_unreadable(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    stored = copy.deepcopy(revision_row(engine, project_id, 1)["design_snapshot"])
    stored["model"]["links"][0]["attributes"]["insulation"] = {"value": "PVC", "source": "data"}
    update_row(
        engine, project_id, 1, design_snapshot=stored, snapshot_sha256=snapshot.sha256_of(stored)
    )
    opened = _opened(client, project_id)
    assert opened["export_status"] == "not_validated"
    assert "snapshot_integrity_failed" in {f["code"] for f in opened["findings"]}


# --------------------------------------------------------------------------- #
# v1 and v2 revisions under a v3 deployment
# --------------------------------------------------------------------------- #

_V1_FORMATS = ("pdf", "json", "dxf", "calculations_pdf")
_V2_FORMATS = (*_V1_FORMATS, "wire_schedule_pdf", "terminal_schedule_pdf", "connection_drawing_pdf")


def test_v1_and_v2_revisions_open_and_export_byte_for_byte_under_v3(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    exports: dict[tuple[str, str], bytes] = {}
    opened_before: dict[str, dict[str, Any]] = {}
    projects: dict[str, tuple[str, tuple[str, ...]]] = {}
    for schema, formats in (("pp-design/1", _V1_FORMATS), ("pp-design/2", _V2_FORMATS)):
        switch(schema)
        project_id = _saved(client, None)
        assert approve(client, project_id, 1).status_code == 200
        projects[schema] = (project_id, formats)
        for fmt in formats:
            response = revision_export(client, project_id, 1, fmt)
            assert response.status_code == 200, (schema, fmt, response.text[:300])
            exports[(schema, fmt)] = response.content
        opened_before[schema] = _opened(client, project_id)
    switch("pp-design/3")
    for schema, (project_id, formats) in projects.items():
        opened = _opened(client, project_id)
        assert opened["approval_current"] is True
        assert opened["export_status"] == opened_before[schema]["export_status"]
        assert opened["snapshot_schema"] == schema
        assert opened["links"] is None
        assert opened["findings"] == opened_before[schema]["findings"]
        for fmt in formats:
            response = revision_export(client, project_id, 1, fmt)
            assert response.status_code == 200, (schema, fmt)
            assert response.content == exports[(schema, fmt)], (schema, fmt)
        assert b'"links"' not in exports[(schema, "json")]


# --------------------------------------------------------------------------- #
# Execution, restricted formats and life safety
# --------------------------------------------------------------------------- #


def test_v3_execution_is_refused_naming_the_link_findings(
    client: TestClient, switch: Switch
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    assert approve(client, project_id, 1).status_code == 200
    refused = revision_export(client, project_id, 1, "json", purpose="execution")
    assert refused.status_code == 409
    body = error(refused)
    assert body["code"] == "execution_package_incomplete"
    blockers = body["params"]["blockers"].split(",")
    assert "link_endpoint_unresolved" in blockers
    assert "link_attribute_unset" in blockers


@pytest.mark.parametrize("fmt", ["devices_csv", "qet", "aml", "plc_st", "cables_csv"])
def test_v3_restricted_formats_stay_refused(client: TestClient, switch: Switch, fmt: str) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [LINK])
    refused = revision_export(client, project_id, 1, fmt)
    assert refused.status_code == 409, refused.text[:300]
    assert error(refused)["code"] == "export_format_unprotected"


@pytest.mark.parametrize(
    "link",
    [
        {"fed_board": "SB-1", "from_end": {"label": "Fire pump panel feed"}},
        {"fed_board": "SB-1", "to_end": {"label": "Sprinkler booster"}},
        {"fed_board": "SB-1", "attributes": {"number": "FIRE PUMP 1"}},
    ],
)
def test_life_safety_words_in_a_link_hold_the_approval(
    client: TestClient, switch: Switch, link: dict[str, Any]
) -> None:
    switch("pp-design/3")
    project_id = _saved(client, [link])
    opened = _opened(client, project_id)
    concerns = [c for c in opened["safety_concerns"] if c["kind"] == "request"]
    assert concerns, opened["safety_concerns"]
    assert all("feeder link to SB-1" in c["where"] for c in concerns)
    refused = approve(client, project_id, 1)
    assert refused.status_code == 409, refused.text[:300]
    assert error(refused)["code"] == "approval_blocked_by_safety"
