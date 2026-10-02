"""A tenant's company profile settings: read and save.

Kept as what the company does differently from the default profile
(``app.design.profile.load_profile``), validated before it is stored, so a
design never meets settings it would refuse. Every function binds the
session to the caller's tenant first (ADR 0003).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.tenancy import bind_tenant
from app.design import profile
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design import CompanySettings
from app.models.tables.design_projects import CompanySettingsRow

logger = structlog.get_logger(__name__)


def _scope_to(session: Session, user: CurrentUser) -> uuid.UUID:
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    bind_tenant(session, tenant)
    return tenant


def get_settings(*, session: Session, user: CurrentUser) -> CompanySettings:
    """The caller's company settings.

    Args:
        session: Open database session.
        user: The authenticated caller.

    Returns:
        The settings as saved; ``None`` settings while none are.
    """
    _scope_to(session, user)
    row = session.scalars(select(CompanySettingsRow)).one_or_none()
    if row is None:
        return CompanySettings()
    return CompanySettings(
        settings=row.settings, updated_by=row.updated_by, updated_at=row.updated_at.isoformat()
    )


def save_settings(
    *, session: Session, user: CurrentUser, settings: dict[str, Any]
) -> CompanySettings:
    """Save the caller's company settings, replacing what was saved.

    Args:
        session: Open database session.
        user: The authenticated caller; recorded as who saved them.
        settings: What the company does differently from the default.

    Returns:
        The settings as saved.

    Raises:
        ValidationError: If a setting is unknown or malformed, naming it.
    """
    tenant = _scope_to(session, user)
    profile.load_profile(settings)
    row = session.scalars(select(CompanySettingsRow)).one_or_none()
    if row is None:
        row = CompanySettingsRow(tenant_id=tenant, settings=settings, updated_by=user.email)
        session.add(row)
    else:
        row.settings = settings
        row.updated_by = user.email
    session.commit()
    logger.info("design.company_settings_saved", tenant_id=str(tenant))
    return get_settings(session=session, user=user)
