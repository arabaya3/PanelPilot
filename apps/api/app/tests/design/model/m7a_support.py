"""Shared synthetic data for the M7a feeder-link tests: MB-1 feeds SB-1.

SYNTHETIC TEST DATA. Board names, loads, template markings, strip markings and
link values here are illustrative only: not a marking or numbering
convention, not an engineering value, and not taken from any customer
document.
"""

from __future__ import annotations

import copy
from typing import Any

#: The synthetic two-board project: MB-1 feeds SB-1 (server-owned feeder).
REQUEST: dict[str, Any] = {
    "info": {"name": "Synthetic two-board example (M7a)"},
    "boards": [
        {
            "name": "MB-1",
            "loads": [{"description": "Lighting", "load": "lighting", "power_kw": "1"}],
        },
        {
            "name": "SB-1",
            "fed_from": "MB-1",
            "feeder_length_m": "25",
            "loads": [{"description": "Sockets", "load": "socket", "power_kw": "2"}],
        },
    ],
}

_BREAKER = {
    "ref": "tenant/synth-m7a-mcb/v1",
    "function": "circuit_breaker",
    "terminals": [
        {"id": "in1", "marking": "1", "kind": "power_line", "required": None, "group": None},
        {"id": "out1", "marking": "2", "kind": "power_line", "required": None, "group": None},
    ],
    "contact_groups": [],
    "parameters": [],
    "provenance": {"source": None, "author": "test suite", "created": "2026-11-10"},
    "review": {
        "status": "unreviewed",
        "reviewer": None,
        "reviewed_at": None,
        "standards": [],
        "scope_note": None,
    },
    "restricted_scope": [],
}

_PATTERN = {
    "ref": "tenant/synth-m7a-out/v1",
    "slots": [{"id": "breaker", "template": _BREAKER["ref"], "device_kind": "circuit_breaker"}],
    "strips": ["out"],
    "points": [{"id": "l", "strip": "out", "offset": 1, "level": 1, "kind": "power_line"}],
    "nets": [
        {
            "role": "line",
            "signal": "L",
            "scope": "binding",
            "members": [{"slot": "breaker", "terminal": "out1"}, {"point": "l", "side": "inner"}],
            "connections": [{"from": 0, "to": 1, "via": "wire"}],
        }
    ],
    "provenance": {"source": "synthetic test data", "author": "test suite", "created": "2026-11-10"},
    "review": copy.deepcopy(_BREAKER["review"]),
}

#: A configuration giving each board one terminal strip point (XF on MB-1, XS on SB-1).
CONFIGURATION: dict[str, Any] = {
    "name": "synthetic M7a link ends",
    "_comment": "SYNTHETIC TEST DATA: illustrative labels for the data shape only.",
    "templates": [_BREAKER],
    "patterns": [_PATTERN],
    "strips": [
        {
            "id": "XF",
            "board": "MB-1",
            "designation": {"function": None, "location": None, "product": "XF"},
            "template": None,
        },
        {
            "id": "XS",
            "board": "SB-1",
            "designation": {"function": None, "location": None, "product": "XS"},
            "template": None,
        },
    ],
    "components": [],
    "bindings": [
        {
            "id": "mb1-feeder",
            "pattern": _PATTERN["ref"],
            "board": "MB-1",
            "circuit": "c2",
            "slots": {},
            "strips": {"out": "XF"},
            "markings": {"l": "F1"},
            "cable": None,
            "wires": {},
        },
        {
            "id": "sb1-out",
            "pattern": _PATTERN["ref"],
            "board": "SB-1",
            "circuit": "c1",
            "slots": {},
            "strips": {"out": "XS"},
            "markings": {"l": "S1"},
            "cable": None,
            "wires": {},
        },
    ],
}


def request(feeder_links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A fresh copy of the synthetic request, with ``feeder_links`` when given."""
    body = copy.deepcopy(REQUEST)
    if feeder_links is not None:
        body["feeder_links"] = feeder_links
    return body


def configuration() -> dict[str, Any]:
    """A fresh copy of the synthetic configuration."""
    return copy.deepcopy(CONFIGURATION)
