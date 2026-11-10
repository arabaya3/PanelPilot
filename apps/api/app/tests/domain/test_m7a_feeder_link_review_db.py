"""M7a reviewer tests: the feeder link between two boards, black-box, against Postgres.

Written by the independent reviewer from ``docs/architecture/m7a-feeder-link.md``
alone, before reading either implementation branch. Drives the real routes
through FastAPI's TestClient against a disposable database
(``TEST_DATABASE_URL``, a ``panelpilot_test_*`` database).

The example is SYNTHETIC: a main board ``MB-1`` feeding a sub-board ``SB-1``.
Every name, label, terminal marking and value here is test data, not a real
installation, marking convention or engineering value.

Written as strict xfail before the feature existed; after integration
(f93690c) every marker was removed because each test passes for real. Shape
helpers follow the as-built notes (contract section 8).
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.design import snapshot
from app.models.schemas.auth import CurrentUser
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
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
    update_row,
)
from app.tests.design.model.v2_support import CONFIGS
from app.tests.domain import review_support

V3 = "pp-design/3"
ATTRIBUTES = (
    "cores",
    "cross_section_mm2",
    "material",
    "insulation",
    "length_m",
    "number",
    "colour",
)
LINK_CODES = {
    "link_endpoint_unresolved",
    "link_attribute_unset",
    "link_value_differs_from_calculation",
}
REVIEW_PDFS = ("pdf", "wire_schedule_pdf", "connection_drawing_pdf")
RESTRICTED = ("devices_csv", "cables_csv", "terminals_csv", "qet", "aml", "plc_st")

#: SYNTHETIC two-board example: MB-1 feeds SB-1 over a server-owned feeder.
REQUEST: dict[str, Any] = {
    "info": {"name": "SYNTHETIC M7a two-board example", "number": "SYN-M7A-1"},
    "boards": [
        {
            "name": "MB-1",
            "supply": {"voltage_v": 400, "phases": 3, "fault_level_ka": 10},
            "loads": [
                {
                    "description": "Synthetic lighting",
                    "load": "lighting",
                    "power_kw": "1",
                    "phases": 1,
                    "length_m": 10,
                }
            ],
        },
        {
            "name": "SB-1",
            "fed_from": "MB-1",
            "feeder_length_m": 25,
            "supply": {"voltage_v": 400, "phases": 3},
            "loads": [
                {
                    "description": "Synthetic sockets",
                    "load": "socket",
                    "power_kw": "2",
                    "phases": 1,
                    "length_m": 10,
                }
            ],
        },
    ],
}

#: SYNTHETIC configuration, adapted from the committed held-out radial case
#: (test data): one bar and one strip on each board, bound to circuit c1 of
#: MB-1 and of SB-1. Markings are illustrative labels, not a convention.
_HELDOUT = CONFIGS["heldout_radial"]["configuration"]
BAR = "tenant/synth-heldout-bar/v1"


def _binding(board: str, strip: str, mark: str) -> dict[str, Any]:
    return {
        "id": f"r-{board.lower()}",
        "pattern": "tenant/synth-heldout-radial/v1",
        "board": board,
        "circuit": "c1",
        "slots": {"mcb": {"device": "c1-breaker"}, "bar": {"component": f"bar-{board.lower()}"}},
        "strips": {"s": strip},
        "markings": {"out": f"{mark}a", "bridge": f"{mark}b"},
        "cable": None,
        "wires": {},
    }


CONFIGURATION: dict[str, Any] = {
    "name": "synthetic m7a two-board radial",
    "_comment": "SYNTHETIC TEST DATA. Markings are illustrative labels only.",
    "templates": copy.deepcopy(_HELDOUT["templates"]),
    "patterns": copy.deepcopy(_HELDOUT["patterns"]),
    "strips": [
        {"id": "XM", "board": "MB-1", "designation": None, "template": None},
        {"id": "XS", "board": "SB-1", "designation": None, "template": None},
    ],
    "components": [
        {"id": f"bar-{b.lower()}", "board": b, "template": BAR, "designation": None}
        for b in ("MB-1", "SB-1")
    ],
    "bindings": [_binding("MB-1", "XM", "M1"), _binding("SB-1", "XS", "S1")],
}

Switch = Callable[[str | None], None]


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(name="switch")
def _switch(monkeypatch: pytest.MonkeyPatch) -> Iterator[Switch]:
    """Set ``DESIGN_SNAPSHOT_SCHEMA``; ``None`` leaves it truly unset (v1)."""

    def set_to(schema: str | None) -> None:
        if schema is None:
            monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
        else:
            monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", schema)
        get_settings.cache_clear()

    yield set_to
    get_settings.cache_clear()


class _People:
    """The tenant's owner (approver) and a member who authors every revision."""

    def __init__(self, caller: _Caller, engine: Engine, tenant: str) -> None:
        self.caller = caller
        self.owner: CurrentUser = caller.user
        self.author = review_support.new_account(engine, tenant)

    def as_author(self) -> None:
        self.caller.user = self.author

    def as_owner(self) -> None:
        self.caller.user = self.owner


@pytest.fixture(name="people")
def _people(caller: _Caller, engine: Engine, tenants: tuple[str, str]) -> _People:
    people = _People(caller, engine, tenants[0])
    people.as_author()
    return people


def _request(feeder_links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    request = copy.deepcopy(REQUEST)
    if feeder_links is not None:
        request["feeder_links"] = feeder_links
    return request


def _post_save(client: TestClient, request: dict[str, Any]) -> Any:
    return client.post(
        "/design/projects", json={"name": "SYNTHETIC M7a", "request": request, "note": "r1"}
    )


def _saved(client: TestClient, request: dict[str, Any]) -> dict[str, Any]:
    response = _post_save(client, request)
    assert response.status_code == 201, response.text[:800]
    body: dict[str, Any] = response.json()
    return body


def _revise(
    client: TestClient,
    project_id: str,
    request: dict[str, Any],
    configuration: dict[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {"request": request, "note": "next"}
    if configuration is not None:
        body["configuration"] = configuration
    return client.post(f"/design/projects/{project_id}/revisions", json=body)


def _opened(client: TestClient, project_id: str, number: int) -> dict[str, Any]:
    response = client.get(f"/design/projects/{project_id}", params={"revision": number})
    assert response.status_code == 200, response.text[:500]
    body: dict[str, Any] = response.json()
    return body


def _revision(opened: dict[str, Any], number: int) -> dict[str, Any]:
    found: dict[str, Any] = next(r for r in opened["revisions"] if r["number"] == number)
    return found


def _links(stored: dict[str, Any]) -> list[dict[str, Any]]:
    """The v3 links collection: top-level of the envelope (or of its model)."""
    assert stored.get("schema") == V3, stored.get("schema")
    found = stored["links"] if "links" in stored else stored["model"]["links"]
    assert isinstance(found, list)
    return found


def _only_link(stored: dict[str, Any]) -> dict[str, Any]:
    links = _links(stored)
    assert len(links) == 1, links
    return links[0]


def _end(link: dict[str, Any], role: str) -> dict[str, Any]:
    found: dict[str, Any] = next(e for e in link["ends"] if e["role"] == role)
    return found


def _refused(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text[:800]
    body = error(response)
    assert body.get("code") == code, response.text[:800]
    return body


def _words(response: Any) -> str:
    assert response.status_code == 200, response.text[:500]
    return " ".join(" ".join(pdf_pages(response.content)).split())


def _feeder_cable(stored: dict[str, Any]) -> dict[str, Any]:
    """The designer's feeder cable MB-1 -> SB-1 in the stored design."""
    design = stored["model"]["design"] if "model" in stored else stored
    board = next(b for b in design["boards"] if b["name"] == "MB-1")
    circuit = next(c for c in board["circuits"] if c.get("feeds") == "SB-1")
    cable: dict[str, Any] = next(c for c in board["cables"] if c["id"] == circuit["cable_id"])
    return cable


def _link_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if f["code"] in LINK_CODES]


def _terminal(opened: dict[str, Any], board: str) -> dict[str, Any]:
    """A TerminalRef on ``board``: the first component terminal of the open revision's schedule."""
    row = next(
        t
        for t in opened["model_v2"]["terminals"]
        if t["board_id"] == board and t["owner_kind"] == "component"
    )
    return {"component": row["owner_id"], "terminal": row["terminal_id"]}


# --------------------------------------------------------------------------- #
# Derivation and round trip
# --------------------------------------------------------------------------- #


def test_v3_derives_exactly_one_link_per_feeder_with_every_attribute_unset(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    saved = _saved(client, _request())
    row = revision_row(engine, saved["id"], 1)
    stored = row["design_snapshot"]
    assert stored["schema"] == V3
    assert snapshot.sha256_of(stored) == row["snapshot_sha256"]
    link = _only_link(stored)
    assert link["origin"] == "derived"
    assert link["id"].startswith("l-")
    assert link["feeder"]["supply_board"] == "MB-1"
    assert link["feeder"]["fed_board"] == "SB-1"
    assert {e["role"]: e["board"] for e in link["ends"]} == {"from": "MB-1", "to": "SB-1"}
    for end in link["ends"]:
        assert end["terminal"] is None
        assert end["resolution"] == "unresolved"
    # Unset is stored as unset: no value, never a default or a designer value.
    assert set(link["attributes"]) == set(ATTRIBUTES)
    for name in ATTRIBUTES:
        assert link["attributes"][name] == {"value": None, "source": "unset"}, name
    assert link["provenance"]["requirement_ref"].endswith(".feeder")
    # A second save of the same request derives the same id (stable).
    again = _saved(client, _request())
    assert _only_link(revision_row(engine, again["id"], 1)["design_snapshot"])["id"] == link["id"]


def test_save_with_feeder_links_then_reopen_keeps_the_same_snapshot_hash(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    entry = {
        "fed_board": "SB-1",
        "from_end": {"label": "SYN outgoing way 3"},
        "to_end": {"label": "SYN incomer"},
        "attributes": {"cores": 4, "number": "SYN-W-01"},
    }
    saved = _saved(client, _request([entry]))
    row = revision_row(engine, saved["id"], 1)
    link = _only_link(row["design_snapshot"])
    assert _end(link, "from")["label"] == "SYN outgoing way 3"
    assert _end(link, "to")["label"] == "SYN incomer"
    assert link["attributes"]["cores"] == {"value": 4, "source": "user"}
    assert link["attributes"]["number"] == {"value": "SYN-W-01", "source": "user"}
    for name in set(ATTRIBUTES) - {"cores", "number"}:
        assert link["attributes"][name]["source"] == "unset", name
        assert link["attributes"][name]["value"] is None, name
    # Provenance is the saving account's, set by the server.
    assert link["provenance"]["entered_by_user_id"] == people.author.id
    assert link["provenance"]["entered_at"]
    opened = _opened(client, saved["id"], 1)
    assert opened["validated"] is True
    assert opened["request"]["feeder_links"][0]["fed_board"] == "SB-1"
    assert revision_row(engine, saved["id"], 1)["snapshot_sha256"] == row["snapshot_sha256"]
    # Saving the reopened request again designs the same links.
    revised = _revise(client, saved["id"], opened["request"])
    assert revised.status_code == 201, revised.text[:800]
    second = revision_row(engine, saved["id"], 2)["design_snapshot"]
    assert _links(second)[0]["attributes"] == link["attributes"]
    assert _links(second)[0]["ends"] == link["ends"]


def test_designer_values_never_appear_as_link_attributes(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    saved = _saved(client, _request([{"fed_board": "SB-1"}]))
    stored = revision_row(engine, saved["id"], 1)["design_snapshot"]
    cable = _feeder_cable(stored)
    link = _only_link(stored)
    designer = {
        str(cable["cores"]),
        str(cable["cross_section_mm2"]),
        str(cable["material"]),
        str(cable["insulation"]),
        str(cable["length_m"]),
    }
    for name, attribute in link["attributes"].items():
        assert attribute["source"] == "unset", name
        assert attribute["value"] is None, name
        assert str(attribute["value"]) not in designer, name
    # The carrier names the feeder cable, it does not copy its values.
    assert link["carrier"]["kind"] in {"cable", "unknown"}
    if link["carrier"]["kind"] == "cable":
        assert link["carrier"]["cable_id"] == cable["id"]


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ([{"fed_board": "SB-9"}], "link_board_unknown"),
        ([{"fed_board": "MB-1"}], "link_not_a_feeder"),
        ([{"fed_board": "SB-1"}, {"fed_board": "SB-1"}], "link_duplicate"),
    ],
    ids=["unknown_board", "not_a_feeder", "duplicate"],
)
def test_a_wrong_board_entry_is_refused_and_nothing_is_saved(
    client: TestClient,
    switch: Switch,
    people: _People,
    entries: list[dict[str, Any]],
    code: str,
) -> None:
    switch(V3)
    _refused(_post_save(client, _request(entries)), 422, code)
    listed = client.get("/design/projects").json()
    assert not listed.get("items", listed if isinstance(listed, list) else [])


@pytest.mark.parametrize(
    "forged",
    [
        {"provenance": {"entered_by_user_id": "00000000-0000-4000-8000-000000000001"}},
        {"provenance": {"entered_at": "2001-01-01T00:00:00Z"}},
        {"entered_by_user_id": "00000000-0000-4000-8000-000000000001"},
    ],
    ids=["provenance_by", "provenance_at", "bare_entered_by"],
)
def test_client_sent_provenance_is_refused(
    client: TestClient, switch: Switch, people: _People, forged: dict[str, Any]
) -> None:
    switch(V3)
    # The same entry without it is accepted: the refusal is for the provenance.
    assert _post_save(client, _request([{"fed_board": "SB-1"}])).status_code == 201
    response = _post_save(client, _request([{"fed_board": "SB-1", **forged}]))
    assert response.status_code == 422, response.text[:500]


@pytest.mark.parametrize(
    "attributes",
    [{"cores": 0}, {"cores": 101}, {"cross_section_mm2": "0"}, {"length_m": "-1"}],
    ids=["cores_0", "cores_101", "csa_0", "length_negative"],
)
def test_out_of_range_attributes_are_refused(
    client: TestClient, switch: Switch, people: _People, attributes: dict[str, Any]
) -> None:
    switch(V3)
    accepted = {"cores": 4, "cross_section_mm2": "16", "length_m": "0"}
    ok = _post_save(client, _request([{"fed_board": "SB-1", "attributes": accepted}]))
    assert ok.status_code == 201, ok.text[:500]
    response = _post_save(client, _request([{"fed_board": "SB-1", "attributes": attributes}]))
    assert response.status_code == 422, response.text[:500]


def _configured_project(
    client: TestClient, engine: Engine, switch: Switch
) -> tuple[str, dict[str, Any]]:
    switch(V3)
    saved = _saved(client, _request())
    revised = _revise(client, saved["id"], _request(), CONFIGURATION)
    assert revised.status_code == 201, revised.text[:800]
    return saved["id"], _opened(client, saved["id"], 2)


def test_terminal_ends_resolve_and_off_board_or_unknown_terminals_are_refused(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    project_id, stored = _configured_project(client, engine, switch)
    mb, sb = _terminal(stored, "MB-1"), _terminal(stored, "SB-1")

    def attempt(from_end: dict[str, Any], to_end: dict[str, Any]) -> Any:
        entry = {"fed_board": "SB-1", "from_end": from_end, "to_end": to_end}
        return _revise(client, project_id, _request([entry]), CONFIGURATION)

    # The from end is on the supply board, the to end on the fed board.
    _refused(attempt({"terminal": sb}, {"terminal": sb}), 422, "link_end_off_board")
    _refused(attempt({"terminal": mb}, {"terminal": mb}), 422, "link_end_off_board")
    unknown = {**mb, "terminal": "no-such-terminal"}
    _refused(attempt({"terminal": unknown}, {}), 422, "link_terminal_unknown")
    ghost = {"component": "c-no-such-component", "terminal": "t1"}
    _refused(attempt({}, {"terminal": ghost}), 422, "link_terminal_unknown")
    done = attempt({"terminal": mb}, {"terminal": sb, "label": "SYN incomer"})
    assert done.status_code == 201, done.text[:800]
    link = _only_link(revision_row(engine, project_id, 3)["design_snapshot"])
    assert _end(link, "from")["resolution"] == "resolved"
    assert _end(link, "from")["terminal"]["component"] == mb["component"]
    assert _end(link, "to")["resolution"] == "resolved"
    findings = revision_row(engine, project_id, 3)["findings"]
    assert not [f for f in findings if f["code"] == "link_endpoint_unresolved"]


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


def test_a_discrepancy_shows_both_values_and_leaves_the_calculation_byte_identical(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    plain = _saved(client, _request())
    plain_stored = revision_row(engine, plain["id"], 1)["design_snapshot"]
    cable = _feeder_cable(plain_stored)
    user_csa = "240" if str(cable["cross_section_mm2"]) != "240" else "185"
    entry = {"fed_board": "SB-1", "attributes": {"cross_section_mm2": user_csa}}
    linked = _saved(client, _request([entry]))
    row = revision_row(engine, linked["id"], 1)
    stored = row["design_snapshot"]
    # The calculation is byte-identical to the same request without links.
    assert snapshot.sha256_of(stored["model"]["design"]) == snapshot.sha256_of(
        plain_stored["model"]["design"]
    )
    assert _feeder_cable(stored) == cable
    differs = [f for f in row["findings"] if f["code"] == "link_value_differs_from_calculation"]
    assert len(differs) == 1, row["findings"]
    shown = json.dumps(differs[0], ensure_ascii=False)
    assert user_csa in shown
    assert str(cable["cross_section_mm2"]) in shown
    assert differs[0]["severity"] != "blocking"
    # Not blocking by itself: the owner approves the author's revision.
    others = [f for f in row["findings"] if f["code"] not in LINK_CODES]
    plain_others = [
        f for f in revision_row(engine, plain["id"], 1)["findings"] if f["code"] not in LINK_CODES
    ]
    assert others == plain_others
    people.as_owner()
    assert approve(client, linked["id"], 1).status_code == 200
    words = _words(revision_export(client, linked["id"], 1, "wire_schedule_pdf"))
    assert user_csa in words
    assert str(cable["cross_section_mm2"]) in words


_COMPLETE = re.compile(r"(?<![a-z])complete(?:d|ly)?\b", re.IGNORECASE)


def test_an_incomplete_link_is_never_reported_complete(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    saved = _saved(client, _request([{"fed_board": "SB-1", "from_end": {"label": "SYN Q4"}}]))
    row = revision_row(engine, saved["id"], 1)
    found = _link_findings(row["findings"])
    unresolved = [f for f in found if f["code"] == "link_endpoint_unresolved"]
    assert len(unresolved) == 2, found  # a label is not a terminal
    unset = [f for f in found if f["code"] == "link_attribute_unset"]
    assert len(unset) == 1, found
    for name in ATTRIBUTES:
        assert name in json.dumps(unset[0]), name
    for finding in found:
        assert finding["severity"] == "warning", finding
        assert not _COMPLETE.search(json.dumps(finding)), finding
    assert not _COMPLETE.search(json.dumps(_links(row["design_snapshot"])))
    opened = _opened(client, saved["id"], 1)
    assert {"link_endpoint_unresolved", "link_attribute_unset"} <= set(opened["execution_blockers"])
    people.as_owner()
    assert approve(client, saved["id"], 1).status_code == 200
    words = _words(revision_export(client, saved["id"], 1, "wire_schedule_pdf"))
    assert "Board-to-board links" in words
    assert "terminal NOT SET" in words
    assert "label given by user" in words
    assert "not a modelled terminal" in words
    assert "not given" in words
    link_section = words[words.index("Board-to-board links") :]
    assert not _COMPLETE.search(link_section)
    drawing = _words(revision_export(client, saved["id"], 1, "connection_drawing_pdf"))
    assert "terminal NOT SET" in drawing
    exported = revision_export(client, saved["id"], 1, "json")
    assert exported.status_code == 200
    assert _links_in_json(exported.json())


def _links_in_json(document: Any) -> list[Any]:
    """Every ``links`` list anywhere in an exported JSON document."""
    found: list[Any] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == "links" and isinstance(inner, list) and inner:
                    found.extend(inner)
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(document)
    return found


@pytest.mark.parametrize("schema", [None, "pp-design/2"], ids=["v1_unset", "v2"])
def test_inter_board_wiring_not_modelled_stays_for_v1_and_v2(
    client: TestClient, engine: Engine, switch: Switch, people: _People, schema: str | None
) -> None:
    switch(schema)
    saved = _saved(client, _request())
    codes = [item["code"] for item in saved["requirements"]["open_items"]]
    assert "inter_board_wiring_not_modelled" in codes
    stored = revision_row(engine, saved["id"], 1)["design_snapshot"]
    assert stored.get("schema") in (None, "pp-design/2")
    assert "links" not in stored


def test_v3_link_replaces_the_open_item_for_its_feeder(
    client: TestClient, switch: Switch, people: _People
) -> None:
    switch(V3)
    saved = _saved(client, _request([{"fed_board": "SB-1"}]))
    codes = [item["code"] for item in saved["requirements"]["open_items"]]
    assert "inter_board_wiring_not_modelled" not in codes
    assert {f["code"] for f in saved["findings"]} >= {"link_attribute_unset"}


# --------------------------------------------------------------------------- #
# Tenancy, integrity, approval
# --------------------------------------------------------------------------- #


def test_another_tenant_gets_404_for_a_linked_revision(
    client: TestClient,
    engine: Engine,
    caller: _Caller,
    tenants: tuple[str, str],
    switch: Switch,
    people: _People,
) -> None:
    switch(V3)
    saved = _saved(client, _request([{"fed_board": "SB-1", "attributes": {"cores": 4}}]))
    assert _only_link(revision_row(engine, saved["id"], 1)["design_snapshot"])
    caller.user = _user(tenants[1], email="m7a-intruder@synthetic.example")
    assert client.get(f"/design/projects/{saved['id']}").status_code == 404
    for fmt in ("json", "wire_schedule_pdf"):
        assert revision_export(client, saved["id"], 1, fmt).status_code == 404
    assert approve(client, saved["id"], 1).status_code == 404
    assert _revise(client, saved["id"], _request([{"fed_board": "SB-1"}])).status_code == 404


def test_a_tampered_link_is_an_integrity_failure(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    saved = _saved(client, _request([{"fed_board": "SB-1"}]))
    stored = copy.deepcopy(revision_row(engine, saved["id"], 1)["design_snapshot"])
    link = _only_link(stored)
    link["attributes"]["cores"] = {"value": 4, "source": "user"}
    update_row(engine, saved["id"], 1, design_snapshot=stored)
    opened = _opened(client, saved["id"], 1)
    assert opened["validated"] is False
    assert "snapshot_integrity_failed" in {f["code"] for f in opened["findings"]}
    for fmt in ("json", "wire_schedule_pdf"):
        _refused(revision_export(client, saved["id"], 1, fmt), 409, "snapshot_integrity_failed")
    people.as_owner()
    _refused(approve(client, saved["id"], 1), 409, "snapshot_integrity_failed")


def test_a_rehashed_tamper_after_approval_is_not_a_current_approval(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    saved = _saved(client, _request([{"fed_board": "SB-1", "to_end": {"label": "SYN in"}}]))
    people.as_owner()
    assert approve(client, saved["id"], 1).status_code == 200
    assert _revision(_opened(client, saved["id"], 1), 1)["approval_current"] is True
    stored = copy.deepcopy(revision_row(engine, saved["id"], 1)["design_snapshot"])
    _end(_only_link(stored), "to")["label"] = "SYN in (altered outside PanelPilot)"
    update_row(
        engine, saved["id"], 1, design_snapshot=stored, snapshot_sha256=snapshot.sha256_of(stored)
    )
    revision = _revision(_opened(client, saved["id"], 1), 1)
    assert revision["approval_current"] is not True
    assert revision["export_status"] != "review_approved"
    exported = revision_export(client, saved["id"], 1, "wire_schedule_pdf")
    if exported.status_code == 200:
        assert "REVIEW-APPROVED" not in _words(exported).upper().replace(" ", "-")


@pytest.mark.parametrize(
    "where",
    ["from_label", "to_label", "number", "colour"],
)
def test_life_safety_words_in_a_link_text_hold_approval(
    client: TestClient, switch: Switch, people: _People, where: str
) -> None:
    switch(V3)
    text_ = "SYN fire pump feed"
    entry: dict[str, Any] = {"fed_board": "SB-1"}
    if where == "from_label":
        entry["from_end"] = {"label": text_}
    elif where == "to_label":
        entry["to_end"] = {"label": text_}
    else:
        entry["attributes"] = {where: text_}
    saved = _saved(client, _request([entry]))
    people.as_owner()
    _refused(approve(client, saved["id"], 1), 409, "approval_blocked_by_safety")


def test_execution_and_restricted_exports_of_a_linked_revision_are_refused(
    client: TestClient, switch: Switch, people: _People
) -> None:
    switch(V3)
    saved = _saved(client, _request([{"fed_board": "SB-1"}]))
    people.as_owner()
    assert approve(client, saved["id"], 1).status_code == 200
    for fmt in ("json", "pdf", "wire_schedule_pdf"):
        body = _refused(
            revision_export(client, saved["id"], 1, fmt, purpose="execution"),
            409,
            "execution_package_incomplete",
        )
        assert "link_endpoint_unresolved" in body["params"]["blockers"].split(","), body
    for fmt in RESTRICTED:
        _refused(revision_export(client, saved["id"], 1, fmt), 409, "export_format_unprotected")


# --------------------------------------------------------------------------- #
# Compatibility: v1 and v2 revisions under a v3 deployment
# --------------------------------------------------------------------------- #

COMPAT_FORMATS = ("pdf", "calculations_pdf", "json", "dxf")
V2_FORMATS = ("wire_schedule_pdf", "terminal_schedule_pdf", "connection_drawing_pdf")


def test_v1_and_v2_revisions_open_export_and_stay_approved_under_v3(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(None)
    v1 = _saved(client, _request())
    switch("pp-design/2")
    v2 = _saved(client, _request())
    assert "schema" not in revision_row(engine, v1["id"], 1)["design_snapshot"]
    assert revision_row(engine, v2["id"], 1)["design_snapshot"]["schema"] == "pp-design/2"
    people.as_owner()
    plan = {v1["id"]: COMPAT_FORMATS, v2["id"]: COMPAT_FORMATS + V2_FORMATS}
    before: dict[tuple[str, str], bytes] = {}
    rows = {}
    for project_id, formats in plan.items():
        assert approve(client, project_id, 1).status_code == 200
        rows[project_id] = revision_row(engine, project_id, 1)
        for fmt in formats:
            exported = revision_export(client, project_id, 1, fmt)
            assert exported.status_code == 200, (fmt, exported.text[:300])
            before[(project_id, fmt)] = exported.content
    switch(V3)
    for project_id, formats in plan.items():
        opened = _opened(client, project_id, 1)
        assert opened["validated"] is True
        revision = _revision(opened, 1)
        assert revision["approval_current"] is True, revision
        assert revision["export_status"] == "review_approved"
        for fmt in formats:
            exported = revision_export(client, project_id, 1, fmt)
            assert exported.status_code == 200, (fmt, exported.text[:300])
            assert exported.content == before[(project_id, fmt)], fmt
        after = revision_row(engine, project_id, 1)
        rows[project_id].pop("updated_at", None)
        after.pop("updated_at", None)
        assert after == rows[project_id]
    # And a new revision now is v3, beside the untouched older ones.
    people.as_author()
    revised = _revise(client, v2["id"], _request())
    assert revised.status_code == 201, revised.text[:500]
    assert revision_row(engine, v2["id"], 2)["design_snapshot"]["schema"] == V3


def test_v1_and_v2_exports_are_byte_stable_before_v3(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    """The precondition of the compatibility test: exporting twice gives the same bytes."""
    switch(None)
    v1 = _saved(client, _request())
    switch("pp-design/2")
    v2 = _saved(client, _request())
    people.as_owner()
    for project_id, formats in (
        (v1["id"], COMPAT_FORMATS),
        (v2["id"], COMPAT_FORMATS + V2_FORMATS),
    ):
        assert approve(client, project_id, 1).status_code == 200
        for fmt in formats:
            first = revision_export(client, project_id, 1, fmt)
            second = revision_export(client, project_id, 1, fmt)
            assert first.status_code == 200, (fmt, first.text[:300])
            assert first.content == second.content, fmt


# --------------------------------------------------------------------------- #
# Review findings on the integrated head (f93690c)
# --------------------------------------------------------------------------- #


def test_choosing_the_ui_material_that_matches_the_calculation_is_not_a_discrepancy(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    switch(V3)
    plain = _saved(client, _request())
    cable = _feeder_cable(revision_row(engine, plain["id"], 1)["design_snapshot"])
    assert cable["material"] == "Cu"
    # "copper" is what the guided UI offers (LINK_MATERIALS) for this cable.
    saved = _saved(client, _request([{"fed_board": "SB-1", "attributes": {"material": "copper"}}]))
    codes = [f["code"] for f in revision_row(engine, saved["id"], 1)["findings"]]
    assert "link_value_differs_from_calculation" not in codes
    # A different metal is still a discrepancy.
    other = _saved(
        client, _request([{"fed_board": "SB-1", "attributes": {"material": "aluminium"}}])
    )
    codes = [f["code"] for f in revision_row(engine, other["id"], 1)["findings"]]
    assert "link_value_differs_from_calculation" in codes


def test_link_texts_are_stored_without_surrounding_spaces(
    client: TestClient, engine: Engine, switch: Switch, people: _People
) -> None:
    """A label and the free-text attributes keep their text, not the spaces around it."""
    switch(V3)
    saved = _saved(
        client,
        _request(
            [
                {
                    "fed_board": "SB-1",
                    "from_end": {"label": "  SYN way Q2  "},
                    "attributes": {"number": " SYN-W-01 "},
                }
            ]
        ),
    )
    links = revision_row(engine, saved["id"], 1)["design_snapshot"]["model"]["links"]
    assert next(e for e in links[0]["ends"] if e["role"] == "from")["label"] == "SYN way Q2"
    assert links[0]["attributes"]["number"]["value"] == "SYN-W-01"
