"""The one way a stored design snapshot is read.

:func:`read_snapshot` identifies the snapshot's schema, refuses one this
software does not read (``snapshot_schema_unknown``), and validates the
content strictly: an unknown field anywhere is an error, never dropped. It
does not check the hash; the caller checks that first, over the stored JSON
as stored.

A ``pp-design/2`` snapshot is read by :func:`codec_v2.parse_model`, which
checks every v2 invariant; its :attr:`VersionedModel.project` is the v1 design
it holds, exactly as stored, so the v1 renderers and checks keep one path.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.design.model import codec_v2, domain, entities, versions
from app.design.model.convert_v1 import from_v1
from app.design.model.serialize import to_stored_v1
from app.models.schemas.design import DesignProject


@dataclass(frozen=True)
class VersionedModel:
    """A stored snapshot as read: its schema, its v1 form and its domain model.

    Attributes:
        schema: The schema it was stored in.
        project: The design as v1, which today's renderers and checks take.
            For a v2 snapshot, the v1 design it holds under ``design``.
        v2: The v2 model, for a v2 snapshot; ``None`` for v1.

    The domain model (:attr:`model`) is built on first use only: the read
    paths that gate approval and export take the v1 form, and should not pay
    for a conversion they discard.
    """

    schema: str
    project: DesignProject
    v2: entities.DesignModelV2 | None = None

    @cached_property
    def model(self) -> domain.DesignModel:
        """The same design as plain domain values, converted once, when first asked for."""
        return from_v1(self.project)


def read_snapshot(stored: Any) -> VersionedModel:
    """Read a stored design snapshot.

    Args:
        stored: The snapshot column's value.

    Returns:
        The snapshot's schema and design.

    Raises:
        SnapshotSchemaUnknownError: If it names a schema this software does not read.
        SnapshotUnreadableError: If it is not a snapshot of its schema: not an
            object, or content that does not validate (an unknown field, a
            missing one, a reference that does not resolve), or nesting
            past the interpreter's recursion limit (a crafted row).
    """
    schema = versions.identify(stored)
    content = versions.payload(stored, schema)
    if schema == versions.V2:
        try:
            v2 = codec_v2.parse_model(content)
        except RecursionError as exc:
            raise versions.SnapshotUnreadableError(
                f"not a {schema} design: nested too deep"
            ) from exc
        return VersionedModel(
            schema=schema, project=DesignProject.model_validate(content["design"]), v2=v2
        )
    # ``identify`` refuses every schema but v1 and v2, so the payload is v1.
    try:
        project = DesignProject.model_validate(content)
    except PydanticValidationError as exc:
        raise versions.SnapshotUnreadableError(
            f"not a {schema} design: {exc.error_count()} error(s)"
        ) from exc
    except RecursionError as exc:
        # MemoryError is not caught: it is not a property of the row, and a
        # process that ran out of memory must not carry on as if it had not.
        raise versions.SnapshotUnreadableError(f"not a {schema} design: nested too deep") from exc
    return VersionedModel(schema=schema, project=project)


def project_to_v1(model: entities.DesignModelV2) -> dict[str, Any]:
    """The v1 design a v2 model holds, exactly as v1 stores it.

    Args:
        model: A v2 model, as read.

    Returns:
        The bare v1 snapshot JSON, byte for byte the ``design`` it was read
        from, for the existing v1 renderers and checks.
    """
    return to_stored_v1(model.design)
