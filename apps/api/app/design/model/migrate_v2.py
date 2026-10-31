"""``pp-design/1`` to ``pp-design/2``: the v1 design kept as is, nothing derived.

:func:`migrate_1_to_2` is pure and deterministic: no registry, clock,
randomness or profile is read; its only input besides the payload is the
frozen legacy template data shipped with it (``templates/``). It adds one
component per v1 device, bound to the legacy template of the device's v1
kind, and embeds and locks those templates. Strips, nets, connections, wires
and assumptions stay empty: deriving any of them would be choosing
engineering content on read, which is forbidden.

A migrated payload is for display and for deriving a new revision only; an
old revision is approved and exported from its stored v1 bytes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.design import snapshot
from app.design.model import ids

#: The frozen legacy template data shipped with this migration.
LEGACY_TEMPLATES_PATH = Path(__file__).parent / "templates" / "legacy_v1_device_kinds.json"


def _load_legacy() -> dict[str, dict[str, Any]]:
    body = json.loads(LEGACY_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {template["function"]: template for template in body["templates"]}


#: By v1 device kind, the legacy template's JSON (one per v1 ``DeviceKind``).
LEGACY_TEMPLATES: dict[str, dict[str, Any]] = _load_legacy()


def legacy_ref(kind: str) -> str:
    """The ref of the legacy template for a v1 device kind.

    Args:
        kind: A v1 ``DeviceKind`` value.

    Returns:
        ``"global/legacy-<kind>/v1"``.

    Raises:
        KeyError: If no legacy template exists for the kind.
    """
    ref: str = LEGACY_TEMPLATES[kind]["ref"]
    return ref


def component_id(board_id: str, device_id: str) -> str:
    """The id of the component mirroring a v1 device (devices are unique per board).

    Args:
        board_id: The v1 board.
        device_id: The v1 device on it.

    Returns:
        A stable opaque id.
    """
    return ids.derive("c", board_id, device_id)


def legacy_components(design: dict[str, Any], skip: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """Components mirroring the v1 devices, each bound to its kind's legacy template.

    Args:
        design: The v1 payload.
        skip: ``(board id, device id)`` pairs another binding already mirrors.

    Returns:
        The component JSON values, unsorted.
    """
    out: list[dict[str, Any]] = []
    for board in design["boards"]:
        for device in board["devices"]:
            if (board["id"], device["id"]) in skip:
                continue
            out.append(
                {
                    "id": component_id(board["id"], device["id"]),
                    "board_id": board["id"],
                    "template_ref": legacy_ref(device["kind"]),
                    "designation": copy.deepcopy(device["designation"]),
                    "parameters": [],
                    "legacy_device_id": device["id"],
                }
            )
    return out


def lock_of(templates: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``registry_lock`` of a list of canonical template JSON values.

    Args:
        templates: The embedded templates, sorted by ref.

    Returns:
        ``{"templates": [{"ref", "digest"}]}``.
    """
    return {"templates": [{"ref": t["ref"], "digest": snapshot.sha256_of(t)} for t in templates]}


def migrate_1_to_2(payload_v1: dict[str, Any]) -> dict[str, Any]:
    """The v2 payload of a v1 payload; the v1 content kept unchanged under ``design``.

    Args:
        payload_v1: A stored v1 snapshot (bare ``DesignProject`` JSON).

    Returns:
        The v2 ``model`` payload, in canonical order. The input is not changed.
    """
    design = copy.deepcopy(payload_v1)
    components = sorted(legacy_components(design, set()), key=lambda c: c["id"])
    used = sorted({c["template_ref"] for c in components})
    by_ref = {t["ref"]: t for t in LEGACY_TEMPLATES.values()}
    templates = [copy.deepcopy(by_ref[ref]) for ref in used]
    return {
        "design": design,
        "templates": templates,
        "registry_lock": lock_of(templates),
        "components": components,
        "strips": [],
        "nets": [],
        "connections": [],
        "wires": [],
        "assumptions": [],
    }
