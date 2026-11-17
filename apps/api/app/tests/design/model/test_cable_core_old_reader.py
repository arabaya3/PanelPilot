"""Old reader, new writer: what the pre-fix reader does with what this code writes.

``pinned/codec_v2_2122d17.py`` is the stored-model reader exactly as on main
before the board-qualified cable-core rule. Every snapshot this code writes
with the write gate off (the default) must read in it identically; a snapshot
that uses one cable id and core on two boards (only written with the gate on)
is refused by it, never misread. SYNTHETIC fixtures; no database.
"""

from __future__ import annotations

import copy

import pytest

from app.design.model import codec_v2, versions
from app.design.model.compose import CompositionRefusedError
from app.design.model.write import snapshot_v2_from_v1
from app.tests.design.model.pinned import codec_v2_2122d17 as old_codec
from app.tests.design.model.v2_support import CONFIGS, config_case


@pytest.mark.parametrize("case", sorted(CONFIGS))
def test_every_configured_fixture_reads_the_same_in_the_old_and_new_reader(case: str) -> None:
    v1, configuration = config_case(case)
    try:
        stored = snapshot_v2_from_v1(v1, configuration)
    except CompositionRefusedError:
        pytest.skip("the composer refuses this fixture (a refusal case)")
    new = codec_v2.parse_model(stored["model"])
    old = old_codec.parse_model(stored["model"])
    assert codec_v2.dump_model(new) == old_codec.dump_model(old) == stored["model"]


def test_both_readers_refuse_a_core_twice_on_one_boards_cable() -> None:
    """Same-board reuse stays refused by the core rule in the new reader, as in the old."""
    checked = 0
    for case in sorted(CONFIGS):
        v1, configuration = config_case(case)
        try:
            stored = snapshot_v2_from_v1(v1, configuration)
        except CompositionRefusedError:
            continue
        model = stored["model"]
        cored = [c for c in model["connections"] if c["core"] is not None]
        if len(cored) < 2:
            continue
        # Two cable-core connections of one board: give the second the first one's core. Only the
        # core is changed, so the refusal can only be the core rule.
        board_of = {n["id"]: n["board_id"] for n in model["nets"]}
        first = cored[0]
        second = next(
            (c for c in cored[1:] if board_of[c["net_id"]] == board_of[first["net_id"]]), None
        )
        if second is None:
            continue
        edited = copy.deepcopy(model)
        twin = next(c for c in edited["connections"] if c["id"] == second["id"])
        twin["core"] = dict(
            twin["core"], cable_id=first["core"]["cable_id"], index=first["core"]["index"]
        )
        with pytest.raises(versions.SnapshotUnreadableError, match="core of one board's cable"):
            codec_v2.parse_model(edited)
        with pytest.raises(versions.SnapshotUnreadableError, match="a core makes two connections"):
            old_codec.parse_model(edited)
        checked += 1
    assert checked > 0, "no fixture has two cable cores on one board"
