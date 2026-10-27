"""The stored, canonical JSON of a domain model.

The bytes are ``snapshot.canonical_json`` of the v1 snapshot exactly as a save
stores it today (``DesignProject.model_dump(mode="json")``), so a model read
from a stored v1 snapshot serialises back to the same bytes and its hash is
the hash recorded with it. M1 writes no other form.
"""

from __future__ import annotations

from typing import Any

from app.design import snapshot
from app.design.model import domain
from app.design.model.convert_v1 import to_v1


def to_stored_v1(model: domain.DesignModel) -> dict[str, Any]:
    """The JSON value a v1 snapshot column holds for a model.

    Args:
        model: The design.

    Returns:
        The bare v1 snapshot (no envelope): ``Decimal``s as strings.

    Raises:
        pydantic.ValidationError: If the model is not valid v1.
    """
    return to_v1(model).model_dump(mode="json")


def canonical_v1(model: domain.DesignModel) -> bytes:
    """The canonical bytes of a model's v1 snapshot, as its hash is taken over.

    Args:
        model: The design.

    Returns:
        UTF-8 canonical JSON.
    """
    return snapshot.canonical_json(to_stored_v1(model))


def sha256_v1(model: domain.DesignModel) -> str:
    """The snapshot hash of a model's v1 snapshot.

    Args:
        model: The design.

    Returns:
        64 lower-case hex digits, as a revision records them.
    """
    return snapshot.sha256_of(to_stored_v1(model))
