"""``pp-design/3`` codec: links read strictly against the v2 model (SYNTHETIC MB-1 to SB-1)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.design import snapshot
from app.design.model import codec_v3, links, versions, write
from app.design.model.migrate_v2 import migrate_2_to_3
from app.design.model.read import read_snapshot
from app.domain.design import build_design
from app.models.schemas.design import ProjectDesignRequest
from app.tests.design.model import m7a_support


def _stored(entries: list[dict[str, Any]] | None = None, configured: bool = False) -> Any:
    request = ProjectDesignRequest.model_validate(m7a_support.request(entries))
    designed, _ = build_design(request)
    stored_v2 = write.snapshot_v2_from_v1(
        designed.model_dump(mode="json"), m7a_support.configuration() if configured else None
    )
    model = read_snapshot(stored_v2).v2
    assert model is not None
    derived = links.derive(
        model,
        request.feeder_links,
        {"MB-1": "b1", "SB-1": "b2"},
        entered_by_user_id="u-1",
        entered_at="2026-11-10T00:00:00+00:00",
    )
    return write.snapshot_v3_from_v2(stored_v2, derived), stored_v2


def test_a_v3_snapshot_reads_back_with_one_link_per_feeder() -> None:
    stored, stored_v2 = _stored([{"fed_board": "SB-1", "attributes": {"cores": 5}}])
    read = read_snapshot(stored)
    assert read.schema == versions.V3
    assert len(read.links) == 1
    assert read.links[0].id == codec_v3.link_id("MB-1", "SB-1")
    # The v2 part is the v2 payload exactly; nets stay board-local.
    assert {k: v for k, v in stored["model"].items() if k != "links"} == stored_v2["model"]
    assert snapshot.sha256_of(stored) != snapshot.sha256_of(stored_v2)


def _altered(change: Any) -> dict[str, Any]:
    stored, _ = _stored()
    out = copy.deepcopy(stored)
    change(out["model"]["links"])
    return out


@pytest.mark.parametrize(
    "change",
    [
        lambda ls: ls.clear(),
        lambda ls: ls.append(copy.deepcopy(ls[0])),
        lambda ls: ls[0].update(id="l-000000000000000000000000"),
        lambda ls: ls[0]["attributes"]["insulation"].update(value="PVC", source="data"),
        lambda ls: ls[0]["attributes"]["cores"].update(value=0, source="user"),
        lambda ls: ls[0]["attributes"]["length_m"].update(value="30.0", source="user"),
        lambda ls: ls[0]["ends"].reverse(),
        lambda ls: ls[0]["ends"][0].update(resolution="resolved"),
        lambda ls: ls[0]["ends"][0].update(terminal={"point": "p-none", "side": "inner"}),
        lambda ls: ls[0]["carrier"].update(cable_id="c1-cable"),
        lambda ls: ls[0].update(extra=1),
        lambda ls: ls[0]["attributes"]["cores"].update(value=1.5, source="user"),
    ],
)
def test_a_link_that_is_not_the_derived_one_is_refused(change: Any) -> None:
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(_altered(change))


def test_a_terminal_on_the_other_board_is_refused() -> None:
    stored, _ = _stored(configured=True)
    model = read_snapshot(stored).v2
    assert model is not None
    other = next(s for s in model.strips if s.board_id == "SB-1").points[0].id
    out = copy.deepcopy(stored)
    end = out["model"]["links"][0]["ends"][0]
    end.update(terminal={"point": other, "side": "inner"}, resolution="resolved")
    with pytest.raises(versions.SnapshotUnreadableError, match="pp-design/3"):
        read_snapshot(out)


def test_the_2_to_3_migration_only_adds_an_empty_collection() -> None:
    _, stored_v2 = _stored()
    payload = stored_v2["model"]
    migrated = migrate_2_to_3(payload)
    assert migrated["links"] == []
    assert "links" not in payload
    assert versions.migrate(payload, versions.V2, versions.V3) == migrated
