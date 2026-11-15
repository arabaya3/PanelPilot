"""Writing ``pp-design/2`` snapshots: the functions the save path calls.

Nothing here decides *whether* a revision is written in v2; that is the
caller's flag (:data:`versions.WRITTEN_BY_DEFAULT` is v1). Every function
returns a snapshot that :func:`read.read_snapshot` accepts: it is read back
before it is returned, so a writer bug is an error here, never a stored row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.design.model import codec_v2, entities, versions
from app.design.model.compose import compose
from app.design.model.migrate_v2 import migrate_1_to_2


def envelope(payload_v2: dict[str, Any]) -> dict[str, Any]:
    """A v2 payload in its stored envelope, checked by the v2 reader first.

    Args:
        payload_v2: The ``model`` value.

    Returns:
        ``{"schema": "pp-design/2", "model": payload_v2}``.

    Raises:
        SnapshotUnreadableError: If the payload is not a valid canonical v2 payload.
    """
    codec_v2.parse_model(payload_v2)
    return {versions.SCHEMA_KEY: versions.V2, versions.MODEL_KEY: payload_v2}


def to_stored_v2(model: entities.DesignModelV2) -> dict[str, Any]:
    """The JSON value a v2 snapshot column holds for a model.

    Args:
        model: The design.

    Returns:
        The envelope; its hash is ``snapshot.sha256_of`` of this value.

    Raises:
        SnapshotUnreadableError: If the model breaks a v2 invariant.
    """
    return envelope(codec_v2.dump_model(model))


def snapshot_v2_from_v1(
    designed_v1: dict[str, Any], configuration: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """A v2 snapshot of a designed v1 snapshot.

    Pure and deterministic. With no configuration, the v1 devices become
    components on their legacy templates and nothing else is derived (the
    migration's content). With one, :func:`compose.compose` builds strips,
    nets, connections and wires from it, or refuses.

    Args:
        designed_v1: The bare v1 snapshot the design service built
            (``DesignProject.model_dump(mode="json")``). Not changed.
        configuration: Templates, patterns and bindings, as data, or ``None``.

    Returns:
        The stored v2 envelope.

    Raises:
        CompositionRefusedError: If the configuration cannot be applied
            without guessing.
        SnapshotUnreadableError: If the v1 snapshot is not a valid stored v1 design.
    """
    if configuration is None:
        return envelope(migrate_1_to_2(designed_v1))
    return envelope(compose(designed_v1, configuration))
