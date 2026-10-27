"""Tests of ``app.design.model.versions``: identification and the migration registry."""

from __future__ import annotations

import pytest

from app.design.model import versions


def test_registry_holds_only_v1() -> None:
    assert {versions.V1} == versions.KNOWN
    assert versions.LATEST == versions.V1
    assert not versions.ENVELOPED
    assert not versions.MIGRATIONS


def test_an_unmarked_object_is_v1() -> None:
    assert versions.identify({"info": {}}) == versions.V1


@pytest.mark.parametrize("marker", ["pp-design/2", "pp-design/1", "", 2, None])
def test_an_unknown_marker_is_refused(marker: object) -> None:
    with pytest.raises(versions.SnapshotSchemaUnknownError) as refused:
        versions.identify({"schema": marker, "model": {}})
    expected = marker if isinstance(marker, str) else repr(marker)
    assert refused.value.code == "snapshot_schema_unknown"
    assert refused.value.params == {"schema": expected}


@pytest.mark.parametrize("stored", [[], "x", 1, None])
def test_a_non_object_is_unreadable(stored: object) -> None:
    with pytest.raises(versions.SnapshotUnreadableError):
        versions.identify(stored)


def test_envelope_payload_is_strict() -> None:
    with pytest.raises(versions.SnapshotUnreadableError):
        versions.payload({"schema": "x", "model": {}, "extra": 1}, "x")
    with pytest.raises(versions.SnapshotUnreadableError):
        versions.payload({"schema": "x", "model": []}, "x")
    assert versions.payload({"schema": "x", "model": {"a": 1}}, "x") == {"a": 1}
    assert versions.payload({"a": 1}, versions.V1) == {"a": 1}


def test_migrations_chain_explicitly_with_a_test_only_registry() -> None:
    """The extension mechanism, shown with dummy schemas that exist only here."""
    order = ("t/1", "t/2", "t/3")
    registry: dict[tuple[str, str], versions.Migration] = {
        ("t/1", "t/2"): lambda v: {**v, "two": True},
        ("t/2", "t/3"): lambda v: {**v, "three": True},
    }
    source = {"one": True}
    out = versions.migrate(source, "t/1", "t/3", order=order, migrations=registry)
    assert out == {"one": True, "two": True, "three": True}
    assert source == {"one": True}
    assert versions.migrate(source, "t/2", "t/2", order=order, migrations=registry) is source
    with pytest.raises(KeyError):
        versions.migrate(source, "t/3", "t/1", order=order, migrations=registry)
    with pytest.raises(KeyError):
        versions.migrate(source, "t/1", "t/3", order=order, migrations={})
