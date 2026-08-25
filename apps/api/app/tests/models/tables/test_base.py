"""Tests for `app/models/tables/base.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

These tests also cover the metadata as a whole, because a mapped class that
cannot be configured is an import-time failure that no other check catches:
mypy does not execute the mappers, and nothing else in the suite imports the
table modules. Without this file, a table missing a primary key ships green and
only fails when someone runs `alembic upgrade head`.
"""

from __future__ import annotations

import pkgutil

import pytest
from sqlalchemy import Table
from sqlalchemy.orm import configure_mappers

import app.models.tables as tables_pkg
from app.models.tables.base import NAMING_CONVENTION, Base


def _import_all_table_modules() -> None:
    """Import every module under ``app.models.tables`` to register its mappers."""
    for module in pkgutil.iter_modules(tables_pkg.__path__):
        __import__(f"{tables_pkg.__name__}.{module.name}")


def test_every_table_module_imports() -> None:
    """Importing the table modules must not raise.

    Alembic imports all of them in ``env.py``; if this fails, so does every
    migration command.
    """
    _import_all_table_modules()
    configure_mappers()


def test_every_table_has_a_primary_key() -> None:
    """SQLAlchemy cannot map a table without one, and Alembic cannot migrate it."""
    _import_all_table_modules()
    without_pk = [name for name, table in Base.metadata.tables.items() if not table.primary_key]
    assert not without_pk, f"tables missing a primary key: {without_pk}"


def test_metadata_uses_the_naming_convention() -> None:
    """Constraint names must be deterministic so migrations can drop them later."""
    assert Base.metadata.naming_convention == NAMING_CONVENTION


@pytest.mark.parametrize("column", ["created_at", "updated_at"])
def test_timestamp_mixin_columns_are_not_nullable(column: str) -> None:
    """A row with no creation time is unauditable."""
    _import_all_table_modules()
    nullable = [
        f"{table.name}.{column}"
        for table in Base.metadata.tables.values()
        if isinstance(table, Table) and column in table.c and table.c[column].nullable
    ]
    assert not nullable, f"nullable timestamp columns: {nullable}"
