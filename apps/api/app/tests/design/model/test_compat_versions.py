"""M1 compatibility, no database: versions, strictness, migrations, legacy reads, boundaries.

* A snapshot with no schema marker reads as ``pp-design/1``.
* An unknown marker is ``snapshot_schema_unknown`` naming the schema, never
  a fallback to v1, and ``pp-design/1`` in an envelope is unknown too.
* A field the schema does not define is refused, never dropped.
* The migration registry applies registered steps in order, and refuses a
  missing step; the dummy schemas exist only inside these tests.
* A v1 row as Phase 1 stored it still passes ``_check_snapshot`` and exports
  the same bytes as before the model existed.
* ``domain.py`` imports no framework.
"""

from __future__ import annotations

import ast
import copy
import sys
import typing
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ConflictError
from app.design import marking, snapshot
from app.design.model import versions
from app.design.model.convert_v1 import from_v1
from app.design.model.migrate_v2 import migrate_1_to_2
from app.design.model.read import read_snapshot
from app.domain import design as design_domain
from app.domain import design_projects
from app.models.schemas.design import (
    Cable,
    Circuit,
    DesignProject,
    Device,
    ExportFormat,
    ProjectDesignRequest,
    Supply,
)
from app.models.tables.design_projects import DesignRevisionRow
from app.tests.design.model.compat_support import DESIGNED, bounds_of, invalid_edges

V1_CASES = sorted(DESIGNED)
RESIDENTIAL = DESIGNED["residential"]["stored"]["design_snapshot"]


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)


# --------------------------------------------------------------------------- #
# Version identification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", V1_CASES)
def test_no_marker_reads_as_v1(case: str) -> None:
    stored = DESIGNED[case]["stored"]["design_snapshot"]
    assert versions.identify(stored) == "pp-design/1"
    assert read_snapshot(stored).schema == "pp-design/1"


def test_v1_and_v2_are_read_and_v1_is_still_written() -> None:
    """M2 reads v2, but writing follows WRITTEN_BY_DEFAULT, never the read order.

    Nothing a test registers may leak into the shipped registry.
    """
    assert versions.V1 == "pp-design/1"
    assert frozenset({"pp-design/1", "pp-design/2"}) == versions.KNOWN
    assert versions.LATEST == "pp-design/2"
    assert versions.WRITTEN_BY_DEFAULT == "pp-design/1"
    assert set(versions.MIGRATIONS) == {("pp-design/1", "pp-design/2")}


@pytest.mark.parametrize(
    "marker",
    ["pp-design/3", "pp-design/999", "", "PP-DESIGN/1", "pp-design/1 ", "other/1"],
)
def test_unknown_marker_is_refused_with_its_schema(marker: str) -> None:
    stored = {"schema": marker, "model": _copy(RESIDENTIAL)}
    with pytest.raises(ConflictError) as raised:
        read_snapshot(stored)
    assert raised.value.code == "snapshot_schema_unknown"
    assert raised.value.params == {"schema": marker}


@pytest.mark.parametrize("marker", [2, None, True, ["pp-design/1"], {"v": 1}])
def test_a_non_string_marker_is_unknown_not_a_crash(marker: Any) -> None:
    with pytest.raises(ConflictError) as raised:
        read_snapshot({"schema": marker, "model": _copy(RESIDENTIAL)})
    assert raised.value.code == "snapshot_schema_unknown"
    # A scalar is echoed as its repr; a container only by its type, so a
    # crafted row cannot make the error body huge.
    expected = f"<{type(marker).__name__}>" if isinstance(marker, list | dict) else repr(marker)
    assert raised.value.params["schema"] == expected


def test_v1_in_an_envelope_is_unknown_not_a_second_form_of_v1() -> None:
    with pytest.raises(ConflictError) as raised:
        read_snapshot({"schema": "pp-design/1", "model": _copy(RESIDENTIAL)})
    assert raised.value.code == "snapshot_schema_unknown"


def test_a_marker_beside_v1_content_is_not_silently_read_as_v1() -> None:
    """A ``schema`` key added to bare v1 content is not stripped and read."""
    stored = {**_copy(RESIDENTIAL), "schema": "pp-design/3"}
    with pytest.raises(ConflictError) as raised:
        read_snapshot(stored)
    assert raised.value.code == "snapshot_schema_unknown"


@pytest.mark.parametrize("stored", [None, [], "pp-design/1", 7])
def test_a_snapshot_that_is_not_an_object_is_unreadable(stored: Any) -> None:
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)


# --------------------------------------------------------------------------- #
# Never drop a field
# --------------------------------------------------------------------------- #


def _with_extra(path: list[Any]) -> dict[str, Any]:
    stored = _copy(RESIDENTIAL)
    target: Any = stored
    for step in path:
        target = target[step]
    target["unexpected_field"] = "kept?"
    return stored


EXTRA_AT: list[list[Any]] = [
    [],
    ["info"],
    ["boards", 0],
    ["boards", 0, "supply"],
    ["boards", 0, "devices", 0],
    ["boards", 0, "devices", 0, "designation"],
    ["boards", 0, "cables", 0],
    ["boards", 0, "circuits", 0],
]


@pytest.mark.parametrize("path", EXTRA_AT, ids=lambda p: "/".join(map(str, p)) or "root")
def test_an_unknown_field_is_refused_not_dropped(path: list[Any]) -> None:
    stored = _with_extra(path)
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)


def test_an_unknown_field_in_a_note_is_refused() -> None:
    stored = _copy(RESIDENTIAL)
    notes = stored["boards"][0]["notes"]
    assert notes, "the residential fixture has notes"
    notes[0]["unexpected_field"] = "x"
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)


# --------------------------------------------------------------------------- #
# Migration registry (dummy schemas, tests only)
# --------------------------------------------------------------------------- #

ORDER = ("t/1", "t/2", "t/3")


def _step(tag: str) -> versions.Migration:
    def migrate(value: dict[str, Any]) -> dict[str, Any]:
        # Pure: a new dict, the input untouched.
        return {**value, "trail": [*value.get("trail", []), tag]}

    return migrate


REGISTRY: dict[tuple[str, str], versions.Migration] = {
    ("t/1", "t/2"): _step("1->2"),
    ("t/2", "t/3"): _step("2->3"),
}


def test_registered_migrations_are_applied_in_order() -> None:
    start: dict[str, Any] = {"trail": []}
    out = versions.migrate(start, "t/1", "t/3", order=ORDER, migrations=REGISTRY)
    assert out["trail"] == ["1->2", "2->3"]
    assert start == {"trail": []}, "a migration must not mutate its input"


def test_a_single_step_is_tested_in_isolation() -> None:
    out = REGISTRY[("t/1", "t/2")]({"a": 1})
    assert out == {"a": 1, "trail": ["1->2"]}
    assert versions.migrate({"a": 1}, "t/2", "t/3", order=ORDER, migrations=REGISTRY) == {
        "a": 1,
        "trail": ["2->3"],
    }


def test_migrating_to_the_same_schema_is_the_identity() -> None:
    value = {"a": 1}
    assert versions.migrate(value, "t/2", "t/2", order=ORDER, migrations=REGISTRY) is value


def test_a_missing_step_is_refused_never_skipped() -> None:
    gapped = {("t/1", "t/2"): REGISTRY[("t/1", "t/2")]}
    with pytest.raises(KeyError):
        versions.migrate({}, "t/1", "t/3", order=ORDER, migrations=gapped)


@pytest.mark.parametrize(("source", "target"), [("t/3", "t/1"), ("t/1", "t/9"), ("t/0", "t/2")])
def test_backwards_or_unknown_migrations_are_refused(source: str, target: str) -> None:
    with pytest.raises((KeyError, ValueError)):
        versions.migrate({}, source, target, order=ORDER, migrations=REGISTRY)


def test_the_default_registry_is_untouched_by_these_tests() -> None:
    assert {(versions.V1, versions.V2): migrate_1_to_2} == versions.MIGRATIONS


# --------------------------------------------------------------------------- #
# Legacy v1 rows through the new read path (no database: a transient row)
# --------------------------------------------------------------------------- #


def _row(case: str) -> DesignRevisionRow:
    body = DESIGNED[case]
    stored = body["stored"]
    return DesignRevisionRow(
        number=1,
        note="",
        author="synthetic@example.com",
        request=ProjectDesignRequest.model_validate(body["request"]).model_dump(mode="json"),
        design_snapshot=_copy(stored["design_snapshot"]),
        snapshot_sha256=stored["snapshot_sha256"],
        profile_sha256=stored["profile_sha256"],
        rules_version=stored["rules_version"],
        catalogue_version=stored["catalogue_version"],
        findings=stored["findings"],
    )


@pytest.mark.parametrize("case", V1_CASES)
def test_a_phase1_row_passes_check_snapshot(case: str) -> None:
    row = _row(case)
    read = design_projects._check_snapshot(row, 1)
    assert read.schema == "pp-design/1"
    assert read.project.model_dump(mode="json") == DESIGNED[case]["stored"]["design_snapshot"]
    # The column itself is never rewritten by reading it.
    assert snapshot.sha256_of(row.design_snapshot) == row.snapshot_sha256


def test_check_snapshot_refuses_an_unknown_schema_with_a_valid_hash() -> None:
    row = _row("residential")
    row.design_snapshot = {"schema": "pp-design/3", "model": _copy(RESIDENTIAL)}
    row.snapshot_sha256 = snapshot.sha256_of(row.design_snapshot)
    with pytest.raises(ConflictError) as raised:
        design_projects._check_snapshot(row, 1)
    assert raised.value.code == "snapshot_schema_unknown"
    assert raised.value.params == {"schema": "pp-design/3"}


def test_check_snapshot_checks_the_hash_before_the_schema() -> None:
    """An unknown marker that breaks the hash is altered first, as the read order says."""
    row = _row("residential")
    row.design_snapshot = {"schema": "pp-design/2", "model": _copy(RESIDENTIAL)}
    with pytest.raises(ConflictError) as raised:
        design_projects._check_snapshot(row, 1)
    assert raised.value.code == "snapshot_integrity_failed"


def test_check_snapshot_refuses_an_unknown_field_as_integrity_failed() -> None:
    row = _row("residential")
    row.design_snapshot = _with_extra(["boards", 0])
    row.snapshot_sha256 = snapshot.sha256_of(row.design_snapshot)
    with pytest.raises(ConflictError) as raised:
        design_projects._check_snapshot(row, 1)
    assert raised.value.code == "snapshot_integrity_failed"


@pytest.mark.parametrize("case", ["residential", "multi_board", "plc", "arabic", "decimal_edges"])
@pytest.mark.parametrize("fmt", [ExportFormat.JSON, ExportFormat.DXF])
def test_export_through_the_read_path_is_byte_identical(case: str, fmt: ExportFormat) -> None:
    """The new read path hands renderers the same design the Phase 1 code did."""
    body = DESIGNED[case]
    stored = body["stored"]["design_snapshot"]
    company = design_domain.resolve_profile(
        ProjectDesignRequest.model_validate(body["request"]).profile
    )
    mark = marking.ExportMark(label=marking.DRAFT_LABEL, identity="synthetic", findings=())
    before = design_domain.render_export(
        designated=DesignProject.model_validate(stored),
        company=company,
        export_format=fmt,
        pricing=None,
        mark=mark,
    )
    after = design_domain.render_export(
        designated=read_snapshot(stored).project,
        company=company,
        export_format=fmt,
        pricing=None,
        mark=mark,
    )
    assert after.content == before.content


# --------------------------------------------------------------------------- #
# Boundaries
# --------------------------------------------------------------------------- #

DOMAIN_PY = Path(__file__).resolve().parents[3] / "design" / "model" / "domain.py"
FORBIDDEN = ("pydantic", "fastapi", "sqlalchemy", "starlette", "app")


def test_domain_imports_no_framework_by_ast() -> None:
    tree = ast.parse(DOMAIN_PY.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "domain.py uses no relative import"
            imported.add(node.module or "")
    roots = {name.split(".")[0] for name in imported}
    assert not roots & set(FORBIDDEN), roots
    assert roots <= {"__future__", "collections", "dataclasses", "decimal", "typing", "enum"}


def test_domain_imports_no_framework_at_runtime() -> None:
    """Importing ``domain.py`` alone, in a fresh interpreter, loads no framework."""
    import subprocess

    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m1_domain', {str(DOMAIN_PY)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['m1_domain'] = module\n"
        "spec.loader.exec_module(module)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('pydantic', 'fastapi', 'sqlalchemy', 'starlette', 'app'))\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# Schema bounds: a value just outside one is refused, never clamped or dropped
# --------------------------------------------------------------------------- #

#: Where in the residential snapshot an instance of each model sits.
_MODEL_AT: dict[type[BaseModel], list[Any]] = {
    Device: ["boards", 0, "devices", 0],
    Cable: ["boards", 0, "cables", 0],
    Circuit: ["boards", 0, "circuits", 0],
    Supply: ["boards", 0, "supply"],
}


def _bounded_decimal_fields() -> list[tuple[type[BaseModel], str, Decimal]]:
    """Every bounded Decimal field of the located models, with each value just outside it.

    Discovered from the schema's metadata, so a bound added later is tested too.
    """
    out: list[tuple[type[BaseModel], str, Decimal]] = []
    for model in _MODEL_AT:
        for name, info in model.model_fields.items():
            if Decimal not in typing.get_args(info.annotation) and info.annotation is not Decimal:
                continue
            for value in invalid_edges(bounds_of(model, name)):
                out.append((model, name, value))
    return out


OUT_OF_BOUNDS = _bounded_decimal_fields()


def test_every_phase1_bound_is_exercised() -> None:
    names = {(m.__name__, n) for m, n, _ in OUT_OF_BOUNDS}
    assert {
        ("Device", "rated_current_a"),
        ("Device", "residual_current_ma"),
        ("Device", "breaking_capacity_ka"),
        ("Cable", "cross_section_mm2"),
        ("Cable", "withstand_ka2s"),
        ("Circuit", "power_kw"),
        ("Circuit", "design_current_a"),
        ("Circuit", "voltage_drop_percent"),
        ("Circuit", "earth_loop_ohm"),
    } <= names


@pytest.mark.parametrize(
    ("model", "name", "value"),
    OUT_OF_BOUNDS,
    ids=[f"{m.__name__}.{n}={v}" for m, n, v in OUT_OF_BOUNDS],
)
def test_a_value_just_outside_a_bound_is_refused(
    model: type[BaseModel], name: str, value: Decimal
) -> None:
    stored = _copy(RESIDENTIAL)
    target: Any = stored
    for step in _MODEL_AT[model]:
        target = target[step]
    target[name] = str(value)
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)
    with pytest.raises(PydanticValidationError):
        from_v1(stored)


def test_a_starter_on_a_non_motor_circuit_is_refused() -> None:
    stored = _copy(RESIDENTIAL)
    circuit = next(c for c in stored["boards"][0]["circuits"] if c["load"] != "motor")
    circuit["starter"] = "dol"
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)
