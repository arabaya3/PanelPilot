"""Tests of ``app.design.model.versions``: identification and the migration registry."""

from __future__ import annotations

import pytest

from app.design.model import versions


def test_registry_reads_v1_and_v2() -> None:
    assert versions.ORDER == (versions.V1, versions.V2)
    assert versions.LATEST == versions.V2
    assert {versions.V2} == versions.ENVELOPED
    assert set(versions.MIGRATIONS) == {(versions.V1, versions.V2)}


def test_writing_does_not_follow_the_read_order() -> None:
    assert versions.WRITTEN_BY_DEFAULT == versions.V1
    assert versions.ORDER[-1] != versions.WRITTEN_BY_DEFAULT


def test_the_order_is_the_registry() -> None:
    assert set(versions.ORDER) == versions.KNOWN
    assert len(set(versions.ORDER)) == len(versions.ORDER)
    assert versions.ORDER[-1] == versions.LATEST
    for before, after in zip(versions.ORDER, versions.ORDER[1:], strict=False):
        assert (before, after) in versions.MIGRATIONS
    assert set(versions.MIGRATIONS) == set(zip(versions.ORDER, versions.ORDER[1:], strict=False))


def test_migrate_defaults_to_the_declared_order() -> None:
    value = {"a": 1}
    assert versions.migrate(value, versions.V1, versions.V1) is value


def test_migrate_refuses_a_schema_outside_the_order() -> None:
    with pytest.raises(KeyError, match="pp-design/9 is not a schema"):
        versions.migrate({}, "pp-design/9", versions.V1)
    with pytest.raises(KeyError, match="pp-design/9 is not a schema"):
        versions.migrate({}, versions.V1, "pp-design/9")


def test_an_int_past_the_digit_limit_is_named_not_printed() -> None:
    with pytest.raises(versions.SnapshotSchemaUnknownError) as refused:
        versions.identify({"schema": 10**5000, "model": {}})
    assert refused.value.params == {"schema": "<int>"}


@pytest.mark.parametrize(
    ("marker", "echoed"),
    [
        ("x" * 1000, "x" * 61 + "..."),
        ({"deep": [1, 2]}, "<dict>"),
        ([1], "<list>"),
        (1.5, "1.5"),
        (True, "True"),
    ],
)
def test_an_echoed_marker_is_short_and_safe(marker: object, echoed: str) -> None:
    with pytest.raises(versions.SnapshotSchemaUnknownError) as refused:
        versions.identify({"schema": marker, "model": {}})
    assert refused.value.params == {"schema": echoed}
    assert len(refused.value.schema) <= versions.MAX_ECHOED_MARKER
    assert len(str(refused.value)) < 200


def test_an_unmarked_object_is_v1() -> None:
    assert versions.identify({"info": {}}) == versions.V1


@pytest.mark.parametrize("marker", ["pp-design/3", "pp-design/1", "", 2, None])
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
    with pytest.raises(KeyError, match="no migration back from t/3 to t/1"):
        versions.migrate(source, "t/3", "t/1", order=order, migrations=registry)
    with pytest.raises(KeyError, match="no migration from t/1 to t/2"):
        versions.migrate(source, "t/1", "t/3", order=order, migrations={})
