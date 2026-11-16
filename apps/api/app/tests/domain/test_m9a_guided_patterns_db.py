"""M9a integration: a connection pattern entered in the guided editor (SYNTHETIC data).

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with the synthetic two-board example of M7a (MB-1 feeds SB-1). Names,
markings and signals are illustrative only: not engineering values.

Contract ``docs/architecture/m9a-guided-patterns.md``: a pattern is the
composer's existing shape in ``configuration.patterns``, entered without JSON,
``user-entered`` and ``unreviewed``; it is stored verbatim with the revision,
checked by the composer whether or not a binding uses it, and an existing
binding composes with it as with any pattern.
"""

from __future__ import annotations

import copy
import io
from typing import Any

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.design.model import anchoring, codec_v2, compose
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    pytestmark,
    revision_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V3,
    Switch,
    _count,
    _new_project,
    _opened,
    _revise,
    _switch,
)

SOURCE = "tenant/synth-src/v1"
LOAD = "tenant/synth-dst/v1"
PATTERN = "tenant/synth-two-slot/v1"
REVIEW: dict[str, Any] = {
    "status": "unreviewed",
    "reviewer": None,
    "reviewed_at": None,
    "standards": [],
    "scope_note": None,
}


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def _definition(ref: str, markings: tuple[str, ...]) -> dict[str, Any]:
    return {
        "ref": ref,
        "function": "unspecified",
        "terminals": [
            {
                "id": f"t-{index:03d}",
                "marking": marking,
                "kind": "unspecified",
                "required": None,
                "group": None,
            }
            for index, marking in enumerate(markings, start=1)
        ],
        "contact_groups": [],
        "parameters": [],
        "provenance": {"source": None, "author": "user-entered", "created": "2026-11-16"},
        "review": dict(REVIEW),
        "restricted_scope": [],
    }


def _pattern(signal: str = "S1") -> dict[str, Any]:
    """A pattern exactly as the guided editor writes it: two slots, one strip role, explicit nets."""
    return {
        "ref": PATTERN,
        "slots": [
            {"id": "src", "template": SOURCE, "device_kind": None},
            {"id": "dst", "template": LOAD, "device_kind": None},
        ],
        "strips": ["out"],
        "points": [
            {"id": "p1", "strip": "out", "offset": 1, "level": 1, "kind": "unspecified"},
            {"id": "p2", "strip": "out", "offset": 2, "level": 1, "kind": "unspecified"},
        ],
        "nets": [
            {
                "role": "n1",
                "signal": signal,
                "scope": "binding",
                "members": [
                    {"slot": "src", "terminal": "t-001"},
                    {"point": "p1", "side": "inner"},
                    {"slot": "dst", "terminal": "t-001"},
                ],
                "connections": [],
            },
            {
                "role": "n2",
                "signal": "S2",
                "scope": "binding",
                "members": [{"slot": "src", "terminal": "t-002"}, {"point": "p2", "side": "inner"}],
                "connections": [],
            },
        ],
        "provenance": {"source": None, "author": "user-entered", "created": "2026-11-16"},
        "review": dict(REVIEW),
    }


def _configuration(pattern: dict[str, Any] | None = None, *, bound: bool = False) -> dict[str, Any]:
    config: dict[str, Any] = {
        "templates": [
            _definition(SOURCE, ("A1", "A2")),
            _definition(LOAD, ("B1",)),
        ],
        "patterns": [pattern if pattern is not None else _pattern()],
        "strips": [
            {
                "id": "strip-xp",
                "board": "MB-1",
                "designation": {"function": None, "location": None, "product": "XP"},
                "template": None,
            }
        ],
        "components": [
            {
                "id": "comp-q1",
                "board": "MB-1",
                "template": SOURCE,
                "designation": {"function": None, "location": None, "product": "KA"},
            },
            {
                "id": "comp-q2",
                "board": "MB-1",
                "template": LOAD,
                "designation": {"function": None, "location": None, "product": "KB"},
            },
        ],
        "bindings": [],
    }
    if bound:
        config["bindings"] = [
            {
                "id": "bind-1",
                "pattern": PATTERN,
                "board": "MB-1",
                "circuit": None,
                "slots": {"src": {"component": "comp-q1"}, "dst": {"component": "comp-q2"}},
                "strips": {"out": "strip-xp"},
                "markings": {"p1": "7", "p2": "8"},
                "cable": None,
                "wires": {},
            }
        ]
    return config


def _saved(client: TestClient, switch: Switch, config: dict[str, Any] | None = None) -> str:
    switch(V3)
    project_id = _new_project(client, V3)
    saved = _revise(
        client,
        project_id,
        m7a_support.request(),
        configuration=config if config is not None else _configuration(),
    )
    assert saved.status_code == 201, saved.text[:600]
    return project_id


def _text(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


# --------------------------------------------------------------------------- #
# Save, reopen, edit
# --------------------------------------------------------------------------- #


def test_a_guided_pattern_saves_once_and_reopens_exactly(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved(client, switch)
    opened = _opened(client, project_id, 2)
    assert opened["configuration"]["patterns"] == [_pattern()]
    # Unbound, it is not embedded in the snapshot (only patterns a binding uses are).
    assert [d["ref"] for d in opened["model_v2"]["definitions"] if d["kind"] == "pattern"] == []


def test_editing_the_pattern_is_a_new_revision_and_the_old_one_is_unchanged(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _saved(client, switch)
    before = revision_row(engine, project_id, 2)
    saved = _revise(
        client,
        project_id,
        m7a_support.request(),
        configuration=_configuration(_pattern(signal="S1-EDITED")),
    )
    assert saved.status_code == 201, saved.text[:600]
    after = revision_row(engine, project_id, 2)
    for column in ("requirements", "requirements_sha256", "design_snapshot", "snapshot_sha256"):
        assert after[column] == before[column], column
    assert (
        revision_row(engine, project_id, 3)["requirements_sha256"] != before["requirements_sha256"]
    )
    assert (
        _opened(client, project_id, 2)["configuration"]["patterns"][0]["nets"][0]["signal"] == "S1"
    )
    assert (
        _opened(client, project_id, 3)["configuration"]["patterns"][0]["nets"][0]["signal"]
        == "S1-EDITED"
    )


# --------------------------------------------------------------------------- #
# Refusals: nothing saved
# --------------------------------------------------------------------------- #


def _unknown_slot_template(p: dict[str, Any]) -> None:
    p["slots"][0]["template"] = "tenant/not-held/v1"


def _terminal_not_on_template(p: dict[str, Any]) -> None:
    p["nets"][1]["members"][0]["terminal"] = "t-009"


def _duplicate_slot_id(p: dict[str, Any]) -> None:
    p["slots"][1]["id"] = "src"


def _duplicate_net_role(p: dict[str, Any]) -> None:
    p["nets"][1]["role"] = "n1"


def _member_names_no_slot(p: dict[str, Any]) -> None:
    p["nets"][0]["members"][0]["slot"] = "gone"


def _member_names_no_point(p: dict[str, Any]) -> None:
    p["nets"][0]["members"][1]["point"] = "gone"


def _member_twice(p: dict[str, Any]) -> None:
    p["nets"][0]["members"].append({"slot": "src", "terminal": "t-001"})


def _member_in_two_nets(p: dict[str, Any]) -> None:
    p["nets"][1]["members"].append({"slot": "dst", "terminal": "t-001"})


def _empty_net(p: dict[str, Any]) -> None:
    p["nets"][1]["members"] = []


def _point_on_no_role(p: dict[str, Any]) -> None:
    p["points"][0]["strip"] = "no-role"


REFUSED: dict[str, tuple[Any, str, bool]] = {
    # case: (mutation, code, names the pattern)
    "unknown_slot_template": (_unknown_slot_template, "unknown_template", True),
    "terminal_not_on_template": (_terminal_not_on_template, "terminal_not_on_template", True),
    "member_twice": (_member_twice, "pattern_invalid", True),
    "member_in_two_nets": (_member_in_two_nets, "contradictory_nets", True),
    "duplicate_slot_id": (_duplicate_slot_id, "pattern_invalid", False),
    "duplicate_net_role": (_duplicate_net_role, "pattern_invalid", False),
    "member_names_no_slot": (_member_names_no_slot, "pattern_invalid", False),
    "member_names_no_point": (_member_names_no_point, "pattern_invalid", False),
    "empty_net": (_empty_net, "pattern_invalid", False),
    "point_on_no_role": (_point_on_no_role, "pattern_invalid", False),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_an_inconsistent_pattern_is_refused_and_nothing_is_saved(
    client: TestClient, switch: Switch, case: str
) -> None:
    project_id = _saved(client, switch)
    mutate, code, names_pattern = REFUSED[case]
    pattern = _pattern()
    mutate(pattern)
    refused = _revise(
        client, project_id, m7a_support.request(), configuration=_configuration(pattern)
    )
    assert refused.status_code == 422, refused.text[:400]
    body = refused.json()
    assert body["code"] == "configuration_refused", body
    assert body["params"]["refusal"] == code, body
    if names_pattern:
        assert body["params"]["pattern"] == PATTERN
    assert _count(client, project_id) == 2


# --------------------------------------------------------------------------- #
# An existing binding composes with the saved pattern (API fixture; M9b builds the UI)
# --------------------------------------------------------------------------- #


def test_an_existing_binding_composes_with_the_saved_pattern(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved(client, switch, _configuration(bound=True))
    model = _opened(client, project_id, 2)["model_v2"]
    # Each net holds exactly the members the pattern names, said by designation and marking.
    nets = {net["signal"]: sorted(net["members"]) for net in model["nets"]}
    assert nets == {"S1": ["KA:A1", "KB:B1", "XP:7 (inner)"], "S2": ["KA:A2", "XP:8 (inner)"]}
    assert [d["ref"] for d in model["definitions"] if d["kind"] == "pattern"] == [PATTERN]
    codes = {finding["code"] for finding in _opened(client, project_id, 2)["findings"]}
    assert "pattern_unreviewed" in codes


def test_connections_added_by_import_generate_wires_in_both_drawings(
    client: TestClient, switch: Switch
) -> None:
    # M9a does not edit connections; an imported pattern's are kept and composed.
    pattern = _pattern()
    pattern["nets"][0]["connections"] = [
        {"from": 0, "to": 1, "via": "wire", "core": None, "label": None},
        {"from": 1, "to": 2, "via": "wire", "core": None, "label": None},
    ]
    project_id = _saved(client, switch, _configuration(pattern, bound=True))
    model = _opened(client, project_id, 2)["model_v2"]
    assert len([c for c in model["connections"] if c["via"] == "wire"]) == 2
    for fmt in ("connection_drawing_pdf", "wire_schedule_pdf"):
        exported = client.post(
            f"/design/projects/{project_id}/revisions/2/export",
            json={"format": fmt, "purpose": "review"},
        )
        assert exported.status_code == 200, exported.text[:400]
        text = _text(exported.content)
        for designation in ("KA", "KB", "XP"):
            assert designation in text, (fmt, designation)


# --------------------------------------------------------------------------- #
# Tenant isolation, review anchoring, approval binding
# --------------------------------------------------------------------------- #


def test_another_tenant_cannot_open_or_carry_the_pattern(
    client: TestClient, switch: Switch, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _saved(client, switch)
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder@example.com")
    assert client.get(f"/design/projects/{project_id}").status_code == 404
    assert (
        _revise(
            client, project_id, m7a_support.request(), configuration=_configuration()
        ).status_code
        == 404
    )
    caller.user = owner
    assert _count(client, project_id) == 2


def test_a_decision_on_the_pattern_does_not_carry_to_an_edited_pattern() -> None:
    before = codec_v2.parse_pattern(compose._ordered_pattern(_pattern()))
    after = codec_v2.parse_pattern(compose._ordered_pattern(_pattern(signal="S1-EDITED")))
    digest = anchoring.definition_digest(before)
    assert anchoring.definition_digest(after) != digest
    decided = anchoring.Decided("approved", "tenant", "rec-1", "2026-11-16T00:00:00Z", ())
    anchor = anchoring.ReviewAnchor(tenant={("pattern", PATTERN, digest): decided})
    assert anchor.status("pattern", PATTERN, digest) == "approved"
    assert anchor.status("pattern", PATTERN, anchoring.definition_digest(after)) == "unreviewed"


def test_an_approval_stays_with_the_revision_it_approved(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved(client, switch)
    assert approve(client, project_id, 2).status_code == 200
    saved = _revise(
        client,
        project_id,
        m7a_support.request(),
        configuration=_configuration(_pattern(signal="S1-EDITED")),
    )
    assert saved.status_code == 201, saved.text[:600]
    # The approval covers revision 2's requirements (with the pattern as it was) only.
    assert _opened(client, project_id, 2)["approval_current"] is True
    assert _opened(client, project_id, 3)["approval_current"] is not True


def test_a_pattern_claiming_a_review_it_has_no_decision_for_is_refused(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved(client, switch)
    claimed = copy.deepcopy(_pattern())
    claimed["review"]["status"] = "approved"
    refused = _revise(
        client, project_id, m7a_support.request(), configuration=_configuration(claimed)
    )
    assert refused.status_code == 422, refused.text[:400]
    body = refused.json()
    assert body["code"] == "configuration_claims_review", body
    assert body["params"]["kind"] == "pattern"
    assert _count(client, project_id) == 2
