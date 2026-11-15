"""Record each tenant's owner: ``tenants.owner_user_id``.

Ownership used to be derived: the tenant's oldest active account. Deactivating
that account (an operator, in the database) silently made the next-oldest
member the owner. The owner is now stored, and changes only by a transfer
(``team.transfer_ownership``) or an operator's ``assign-owner``.

Backfill, deterministic, never granting more than the derived rule did:

* the tenant's oldest account (by ``created_at``, of every account, active
  or not) becomes its owner when it is active and no other account shares
  its ``created_at``;
* otherwise the owner stays NULL, and the tenant's admins keep managing it:

  - ``no_accounts``: no account at all (an unclaimed trial). Not a problem:
    the claiming signup sets the owner.
  - ``first_account_inactive``: the oldest account is deactivated. The old
    rule handed ownership to the next-oldest member; that is exactly the
    silent grant this column removes, so no one is chosen.
  - ``created_at_tie``: two or more accounts share the oldest ``created_at``;
    the old rule broke the tie by id, which is arbitrary.

  Several admins are not ambiguous: an admin keeps admin's powers whoever
  owns the tenant, so the oldest account is still the owner.

Counts of each case (no ids, no personal data) are logged. The tenants left
without an owner are listed by the worker's ``owner-report`` command, which
flags those with no active admin either: nobody there can manage the team
until an operator runs ``assign-owner`` (docs/design-readiness/
phase-1-export-integrity.md, Permissions).

Downgrade drops the column: transfers made since are lost, and ownership is
derived again.

Revision ID: a7d3e9f1c2b4
Revises: f8c4a2e6d9b3
Create Date: 2026-10-28
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7d3e9f1c2b4"
down_revision: str | None = "f8c4a2e6d9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: Each tenant's case, from its oldest account. Shared by the backfill and
#: the counts, so what is logged is what was done.
_CASES = """
    WITH first AS (
        SELECT DISTINCT ON (u.tenant_id)
               u.tenant_id, u.id, u.is_active, u.created_at
          FROM users u
         ORDER BY u.tenant_id, u.created_at, u.id
    )
    SELECT t.id AS tenant_id,
           f.id AS first_id,
           CASE
             WHEN f.id IS NULL THEN 'no_accounts'
             WHEN NOT f.is_active THEN 'first_account_inactive'
             WHEN (SELECT count(*) FROM users u
                    WHERE u.tenant_id = t.id AND u.created_at = f.created_at) > 1
               THEN 'created_at_tie'
             ELSE 'owner_set'
           END AS outcome,
           EXISTS (
             SELECT 1 FROM users u
               JOIN user_roles ur ON ur.user_id = u.id
               JOIN roles r ON r.id = ur.role_id
              WHERE u.tenant_id = t.id AND u.is_active AND r.name = 'admin'
           ) AS has_admin
      FROM tenants t
      LEFT JOIN first f ON f.tenant_id = t.id
"""


def upgrade() -> None:
    """Add the column and its same-tenant key, then backfill unambiguous owners."""
    op.create_unique_constraint("uq_users_id_tenant_id", "users", ["id", "tenant_id"])
    op.add_column("tenants", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.create_index("ix_tenants_owner_user_id", "tenants", ["owner_user_id"])
    op.create_foreign_key(
        "fk_tenants_owner_user_id_users",
        "tenants",
        "users",
        ["owner_user_id", "id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )

    bind = op.get_bind()
    bind.execute(sa.text(f"""
            UPDATE tenants t SET owner_user_id = c.first_id
              FROM ({_CASES}) c
             WHERE c.tenant_id = t.id AND c.outcome = 'owner_set'
            """))
    counts = bind.execute(sa.text(f"""
            SELECT outcome,
                   count(*) AS tenants,
                   count(*) FILTER (WHERE NOT has_admin) AS without_admin
              FROM ({_CASES}) c
             GROUP BY outcome ORDER BY outcome
            """)).all()
    lines = [
        f"tenant owner backfill: {outcome} tenants={tenants} without_active_admin={without}"
        for outcome, tenants, without in counts
    ]
    unresolved = sum(
        without
        for outcome, _tenants, without in counts
        if outcome in ("first_account_inactive", "created_at_tie")
    )
    if unresolved:
        lines.append(
            f"tenant owner backfill: {unresolved} tenants have no owner and no active admin; "
            "list them with the worker's owner-report and resolve with assign-owner"
        )
    # Printed as well as logged: the migration environment configures no
    # logging, and an operator running the upgrade must see these counts.
    for line in lines:
        logger.info(line)
        print(line, file=sys.stderr)


def downgrade() -> None:
    """Drop the owner; ownership is derived from the oldest active account again."""
    op.drop_constraint("fk_tenants_owner_user_id_users", "tenants", type_="foreignkey")
    op.drop_index("ix_tenants_owner_user_id", table_name="tenants")
    op.drop_column("tenants", "owner_user_id")
    op.drop_constraint("uq_users_id_tenant_id", "users", type_="unique")
