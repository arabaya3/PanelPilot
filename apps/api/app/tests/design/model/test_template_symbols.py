"""Template symbol data (M4): optional, strict, canonical, and covered by the template's digest."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.design.model import anchoring, codec_v2, versions
from app.tests.design.model.v2_support import CONFIGS

LEGACY = Path(codec_v2.__file__).with_name("templates") / "legacy_v1_device_kinds.json"


def _template() -> dict[str, Any]:
    """The synthetic relay template of the control-relay drawing case (it has a symbol)."""
    body = CONFIGS["drawing_control_relay"]["configuration"]
    found = next(t for t in body["templates"] if t["ref"] == "tenant/synth-drw-relay/v1")
    template = copy.deepcopy(found)
    shapes = template["symbol"]["shapes"]
    template["symbol"]["shapes"] = sorted(
        shapes, key=lambda s: (s["kind"], tuple(s["points"]), s["text"] or "")
    )
    return template


def test_a_template_without_a_symbol_keeps_its_canonical_json_and_digest() -> None:
    for raw in json.loads(LEGACY.read_text(encoding="utf-8"))["templates"]:
        template = codec_v2.parse_template(raw)
        assert template.symbol is None
        assert codec_v2.dump_template(template) == raw
        assert "symbol" not in codec_v2.dump_template(template)


def test_a_symbol_round_trips_and_is_part_of_the_digests() -> None:
    raw = _template()
    template = codec_v2.parse_template(raw)
    assert template.symbol is not None
    assert codec_v2.dump_template(template) == raw
    bare = {k: v for k, v in raw.items() if k != "symbol"}
    without = codec_v2.parse_template(bare)
    # The symbol is reviewed content: changing it changes what a review binds to.
    assert codec_v2.template_digest(template) != codec_v2.template_digest(without)
    assert anchoring.definition_digest(template) != anchoring.definition_digest(without)


def _refused(raw: dict[str, Any]) -> None:
    with pytest.raises(versions.SnapshotUnreadableError):
        codec_v2.parse_template(raw)


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s["anchors"].append({"terminal": "nope", "x": 0, "y": 0}),
        lambda s: s["anchors"].__setitem__(0, {**s["anchors"][0], "x": 99}),
        lambda s: s["shapes"].append({"kind": "line", "points": [0, 0, 1], "text": None}),
        lambda s: s["shapes"].append({"kind": "spline", "points": [0, 0, 1, 1], "text": None}),
        lambda s: s["shapes"].append({"kind": "text", "points": [0, 0], "text": None}),
        lambda s: s["shapes"].append({"kind": "line", "points": [0, 0, 1, 1], "text": "x"}),
        lambda s: s["shapes"].append({"kind": "line", "points": [0, 0, 1, 1.5], "text": None}),
        lambda s: s["shapes"].append({"kind": "line", "points": [0, 0, 1, -1], "text": None}),
        lambda s: s["shapes"].append({"kind": "line", "points": [0, 0, 1, 101], "text": None}),
        lambda s: s["shapes"].reverse(),
        lambda s: s.__setitem__("width", 0),
        lambda s: s.__setitem__("height", 101),
        lambda s: s.__setitem__("rating", "x"),
        lambda s: s["shapes"].extend(
            {"kind": "line", "points": [0, 0, 1, n], "text": None} for n in range(65)
        ),
    ],
)
def test_a_malformed_symbol_is_refused(edit: Any) -> None:
    raw = _template()
    edit(raw["symbol"])
    _refused(raw)


def test_every_configured_symbol_names_only_its_templates_terminals() -> None:
    seen = 0
    for body in CONFIGS.values():
        for raw in body["configuration"].get("templates", []):
            if "symbol" not in raw:
                continue
            seen += 1
            terminals = {t["id"] for t in raw["terminals"]}
            assert {a["terminal"] for a in raw["symbol"]["anchors"]} <= terminals
    assert seen >= 2
