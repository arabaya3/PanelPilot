"""Tests for `app/domain/company_settings.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.errors import ValidationError
from app.domain import company_settings
from app.models.schemas.auth import CurrentUser
from app.models.tables.base import Base
from app.models.tables.tenant import TenantRow

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: settings are JSONB and tenant isolation is the database's",
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenants")
def _tenants(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Two tenants, no settings saved."""
    with sessionmaker(bind=engine)() as session:
        session.execute(text("TRUNCATE company_settings"))
        ids = []
        for slug in ("settings-a", "settings-b"):
            found = session.execute(
                text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": slug}
            ).scalar_one_or_none()
            if found is None:
                row = TenantRow(slug=slug, name=slug)
                session.add(row)
                session.flush()
                found = row.id
            ids.append(found)
        session.commit()
        return ids[0], ids[1]


def _user(tenant: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        id=str(uuid.uuid4()), email="eng@example.com", tenant_id=str(tenant), roles=frozenset()
    )


def test_get_settings_is_empty_until_saved(tenants, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with sessionmaker(bind=engine)() as session:
        assert company_settings.get_settings(session=session, user=_user(a)).settings is None


def test_save_settings_replaces_and_stays_with_the_tenant(tenants, engine) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    first = {"key": "acme", "name": "Acme", "max_circuits_per_rcd": 8}
    with sessionmaker(bind=engine)() as session:
        saved = company_settings.save_settings(session=session, user=_user(a), settings=first)
    assert saved.settings == first
    assert saved.updated_by == "eng@example.com"
    second = {"key": "acme", "demand_factors": {"socket": "0.5"}}
    with sessionmaker(bind=engine)() as session:
        company_settings.save_settings(session=session, user=_user(a), settings=second)
    with sessionmaker(bind=engine)() as session:
        assert company_settings.get_settings(session=session, user=_user(a)).settings == second
    with sessionmaker(bind=engine)() as session:
        assert company_settings.get_settings(session=session, user=_user(b)).settings is None


def test_save_settings_refuses_what_a_design_would(tenants, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with sessionmaker(bind=engine)() as session, pytest.raises(ValidationError) as refused:
        company_settings.save_settings(
            session=session, user=_user(a), settings={"key": "acme", "bogus": 1}
        )
    assert refused.value.code == "profile_unknown_settings"
