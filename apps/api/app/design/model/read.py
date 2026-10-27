"""The one way a stored design snapshot is read.

:func:`read_snapshot` identifies the snapshot's schema, refuses one this
software does not read (``snapshot_schema_unknown``), and validates the
content strictly: an unknown field anywhere is an error, never dropped. It
does not check the hash; the caller checks that first, over the stored JSON
as stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.design.model import domain, versions
from app.design.model.convert_v1 import from_v1
from app.models.schemas.design import DesignProject


@dataclass(frozen=True)
class VersionedModel:
    """A stored snapshot as read: its schema, its v1 form and its domain model.

    Attributes:
        schema: The schema it was stored in.
        project: The design as v1, which today's renderers and checks take.
        model: The same design as plain domain values.
    """

    schema: str
    project: DesignProject
    model: domain.DesignModel


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
            missing one, a reference that does not resolve).
    """
    schema = versions.identify(stored)
    content = versions.payload(stored, schema)
    # Only v1 is known: ``identify`` refuses every other schema, so the
    # payload is v1. A later schema adds its own reader here.
    try:
        project = DesignProject.model_validate(content)
    except PydanticValidationError as exc:
        raise versions.SnapshotUnreadableError(
            f"not a {schema} design: {exc.error_count()} error(s)"
        ) from exc
    return VersionedModel(schema=schema, project=project, model=from_v1(project))
