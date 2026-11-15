"""Which schema a stored design snapshot is in, and how one schema migrates to the next.

How a version is identified (ADR-2, M1):

* A stored snapshot with no top-level ``"schema"`` key is ``pp-design/1`` by
  definition: that is every snapshot saved so far, the bare JSON of a
  ``DesignProject``. v1 has no ``schema`` field and refuses unknown fields,
  so the key can never be mistaken for v1 content.
* Any later schema is stored in an envelope,
  ``{"schema": "pp-design/N", "model": {...}}``, with no other top-level key.
  The snapshot hash covers the stored JSON exactly as stored, envelope and
  marker included, so a marker cannot be changed without failing the hash.
* v1 is never enveloped: an envelope naming ``pp-design/1`` would give one
  design two byte forms and two hashes, so it is refused as unknown.

Two schemas exist. ``pp-design/2`` (M2) is enveloped and adds templates,
components, strips, nets, connections and wires to the v1 design, which it
holds unchanged under ``design``. :data:`MIGRATIONS` holds the pure
``migrate_1_to_2``.

**Reading is decoupled from writing.** :data:`ORDER` and :data:`KNOWN` say what
this software *reads*; :data:`WRITTEN_BY_DEFAULT` says what a new revision is
*written* in, and stays ``pp-design/1`` until the owner turns v2 writing on
(D-M2-3). Adding a schema to :data:`ORDER` never changes what is written.

A migration is run on read only, to display or to derive a new revision;
a stored snapshot is never rewritten, and an old revision is never exported
as if it were the migrated one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.core.errors import ConflictError

#: The schema of every snapshot stored with no marker.
V1 = "pp-design/1"

#: The top-level key naming an enveloped snapshot's schema.
SCHEMA_KEY = "schema"

#: The enveloped snapshot's payload key.
MODEL_KEY = "model"

#: The M2 schema: the v1 design plus components, terminals, nets and wires.
V2 = "pp-design/2"

#: Schemas stored in an envelope: every schema after v1.
ENVELOPED: frozenset[str] = frozenset({V2})

#: Every schema this software reads, oldest first: the declared migration order.
#: Each adjacent pair has an entry in :data:`MIGRATIONS` (tested).
ORDER: tuple[str, ...] = (V1, V2)

#: Every schema this software reads.
KNOWN: frozenset[str] = frozenset(ORDER)

#: The newest schema this software reads: the last in :data:`ORDER`. It is
#: *not* what new revisions are written in; see :data:`WRITTEN_BY_DEFAULT`.
LATEST = ORDER[-1]

#: The schema a new revision is written in unless the caller explicitly asks
#: for another. v1, as in Phase 1 and M1: writing v2 is a separate, flagged
#: decision (D-M2-3) and does not follow :data:`ORDER`.
WRITTEN_BY_DEFAULT = V1

#: The longest schema marker a message or error parameter echoes; a stored
#: marker is untrusted and may be any length.
MAX_ECHOED_MARKER = 64

#: A migration: a pure function from one schema's JSON payload to the next's.
Migration = Callable[[dict[str, Any]], dict[str, Any]]

#: Explicit migrations, by ``(from, to)``. Filled at the end of this module.
MIGRATIONS: dict[tuple[str, str], Migration] = {}


class SnapshotSchemaUnknownError(ConflictError):
    """A stored snapshot names a schema this software does not read."""

    def __init__(self, schema: str) -> None:
        """Record the schema named.

        Args:
            schema: The marker as stored, as text.
        """
        super().__init__(
            f"the stored design is in schema {schema!r}, which this version does not read",
            code="snapshot_schema_unknown",
            params={"schema": schema},
        )
        self.schema = schema


class SnapshotUnreadableError(ValueError):
    """A stored snapshot's shape is not a snapshot (not an object, a bad envelope)."""


def identify(stored: Any) -> str:
    """The schema a stored snapshot is in.

    Args:
        stored: The snapshot as read from the database.

    Returns:
        A schema in :data:`KNOWN`.

    Raises:
        SnapshotSchemaUnknownError: If it names a schema this software does not
            read, ``pp-design/1`` in an envelope included.
        SnapshotUnreadableError: If it is not a JSON object.
    """
    if not isinstance(stored, dict):
        raise SnapshotUnreadableError("a stored design is a JSON object")
    if SCHEMA_KEY not in stored:
        return V1
    marker = stored[SCHEMA_KEY]
    if not isinstance(marker, str) or marker not in ENVELOPED:
        raise SnapshotSchemaUnknownError(_echoed(marker))
    return marker


def _echoed(marker: Any) -> str:
    """A stored marker as text that is safe to echo: a scalar's form, cut short.

    A container is named by its type only: its ``repr`` could be megabytes, or
    recurse past the interpreter's limit on a crafted row.
    """
    if isinstance(marker, str):
        text = marker
    elif marker is None or isinstance(marker, bool | float):
        text = repr(marker)
    elif isinstance(marker, int):
        # An int past the interpreter's digit limit has no text form.
        text = repr(marker) if abs(marker) < 10**MAX_ECHOED_MARKER else "<int>"
    else:
        text = f"<{type(marker).__name__}>"
    if len(text) > MAX_ECHOED_MARKER:
        return text[: MAX_ECHOED_MARKER - 3] + "..."
    return text


def payload(stored: dict[str, Any], schema: str) -> dict[str, Any]:
    """The model JSON inside a stored snapshot of a known schema.

    Args:
        stored: The snapshot as read, already identified.
        schema: Its schema, from :func:`identify`.

    Returns:
        The snapshot itself for v1; an envelope's ``model`` otherwise.

    Raises:
        SnapshotUnreadableError: If an envelope holds any key besides its marker
            and model, or a model that is not an object.
    """
    if schema == V1:
        return stored
    extra = set(stored) - {SCHEMA_KEY, MODEL_KEY}
    if extra:
        raise SnapshotUnreadableError(f"unknown envelope keys: {', '.join(sorted(extra))}")
    model = stored.get(MODEL_KEY)
    if not isinstance(model, dict):
        raise SnapshotUnreadableError("an envelope's model is a JSON object")
    return model


def migrate(
    value: dict[str, Any],
    source: str,
    target: str,
    *,
    order: tuple[str, ...] = ORDER,
    migrations: Mapping[tuple[str, str], Migration] = MIGRATIONS,
) -> dict[str, Any]:
    """Migrate a payload step by step from one schema to a later one.

    Args:
        value: The payload in ``source``.
        source: Its schema.
        target: The schema wanted.
        order: Every schema, oldest first; each step goes to the next.
            :data:`ORDER` unless a test passes its own.
        migrations: The registry; :data:`MIGRATIONS` unless a test passes its own.

    Returns:
        The payload in ``target``; ``value`` itself when they are the same.

    Raises:
        KeyError: If either schema is not in ``order``, ``target`` is older,
            or a step has no registered migration. Never a silent skip.
    """
    for schema in (source, target):
        if schema not in order:
            raise KeyError(f"{schema} is not a schema in the migration order")
    start, end = order.index(source), order.index(target)
    if end < start:
        raise KeyError(f"no migration back from {source} to {target}")
    for before, after in zip(order[start:end], order[start + 1 : end + 1], strict=True):
        step = migrations.get((before, after))
        if step is None:
            raise KeyError(f"no migration from {before} to {after}")
        value = step(value)
    return value


# Registered last: the migration module needs nothing from this one, and
# importing it here keeps the registry complete whenever ``versions`` is imported.
from app.design.model.migrate_v2 import migrate_1_to_2  # noqa: E402

MIGRATIONS[(V1, V2)] = migrate_1_to_2
