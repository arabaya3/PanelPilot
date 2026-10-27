"""Tests of ``app.design.model.serialize``: the stored v1 bytes and hash, unchanged."""

from __future__ import annotations

import pytest

from app.design import snapshot
from app.design.model import convert_v1, serialize
from app.models.schemas.design import DesignProject
from app.tests.design.model.test_convert_v1 import tower


@pytest.mark.parametrize("which", ["hall", "tower"])
def test_stored_bytes_and_hash_are_identical(which: str, hall_project: DesignProject) -> None:
    project = hall_project if which == "hall" else tower()
    stored = project.model_dump(mode="json")
    model = convert_v1.from_v1(stored)
    assert serialize.to_stored_v1(model) == stored
    assert serialize.canonical_v1(model) == snapshot.canonical_json(stored)
    assert serialize.sha256_v1(model) == snapshot.sha256_of(stored)


def test_the_stored_form_has_no_envelope(hall_project: DesignProject) -> None:
    assert "schema" not in serialize.to_stored_v1(convert_v1.from_v1(hall_project))
