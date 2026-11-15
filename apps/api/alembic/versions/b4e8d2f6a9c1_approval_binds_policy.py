"""Bind each approval to the policy it was granted under, and freeze the approver's name.

Adds to ``design_project_revisions``, all nullable:

* ``approved_profile_sha256``, ``approved_rules_version``,
  ``approved_catalogue_version``: the resolved profile hash and the rules and
  catalogue versions the approval was granted under;
* ``approved_findings_policy``: the SHA-256 of the canonical findings
  classification of the approved design under the policy then;
* ``approved_display_name``: the approver's display name as it was then (``""``
  for an account with none), which exports print instead of resolving it.

Backfill of rows approved before this migration, deterministic:

* the profile hash, rules and catalogue versions are copied from the
  revision's own stored ``profile_sha256``, ``rules_version`` and
  ``catalogue_version``. An approval was only granted to a revision that was
  not stale, so those were the applicable values at approval time;
* ``approved_findings_policy`` stays NULL: the code reads NULL as the
  classification of the revision's own stored findings, which an approval
  required to match the policy at approval time. It is not computed here
  because the findings policy is code, not data, and a migration must not
  depend on the version of it that happens to be installed;
* ``approved_display_name`` is the approving account's display name now (at
  migration time), resolved as exports resolved it until now: by
  ``approved_user_id`` in the revision's tenant, else by ``approved_account``
  (email, case-insensitive) in the tenant; a name that is blank or holds an
  ``@`` is no name. No usable name is stored as ``""`` ("a team member").

Counts of approved rows backfilled (no ids, no names) are printed.

Downgrade drops the columns: approvals count again on the snapshot alone, and
approver names are resolved live again.

Revision ID: b4e8d2f6a9c1
Revises: a7d3e9f1c2b4
Create Date: 2026-10-28
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4e8d2f6a9c1"
down_revision: str | None = "a7d3e9f1c2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "design_project_revisions"

#: The display name an export printed for each approved row until now.
_NAME = """
    SELECT NULLIF(btrim(u.full_name), '')
      FROM users u
     WHERE u.tenant_id = r.tenant_id
       AND (CASE WHEN r.approved_user_id IS NOT NULL
                 THEN u.id = r.approved_user_id
                 ELSE lower(u.email) = lower(r.approved_account) END)
     ORDER BY u.id
     LIMIT 1
"""


def upgrade() -> None:
    """Add the columns, then backfill rows approved before them."""
    op.add_column(_TABLE, sa.Column("approved_profile_sha256", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("approved_rules_version", sa.String(40), nullable=True))
    op.add_column(_TABLE, sa.Column("approved_catalogue_version", sa.String(40), nullable=True))
    op.add_column(_TABLE, sa.Column("approved_findings_policy", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("approved_display_name", sa.String(200), nullable=True))

    bind = op.get_bind()
    policy = bind.execute(sa.text(f"""
            UPDATE {_TABLE}
               SET approved_profile_sha256 = profile_sha256,
                   approved_rules_version = rules_version,
                   approved_catalogue_version = catalogue_version
             WHERE approved_at IS NOT NULL
            """)).rowcount
    # Strip first, then refuse a name that is the email (it holds an "@"),
    # as ``design_projects._display_name`` does.
    bind.execute(sa.text(f"""
            UPDATE {_TABLE} r
               SET approved_display_name = COALESCE(
                     (SELECT CASE WHEN n.name LIKE '%@%' THEN NULL ELSE n.name END
                        FROM ({_NAME}) AS n(name)),
                     '')
             WHERE r.approved_at IS NOT NULL
            """))
    named = bind.execute(sa.text(f"""
            SELECT count(*) FILTER (WHERE approved_display_name <> ''),
                   count(*) FILTER (WHERE approved_display_name = '')
              FROM {_TABLE} WHERE approved_at IS NOT NULL
            """)).one()
    lines = [
        f"approval policy backfill: approved_rows={policy}",
        f"approver name backfill: named={named[0]} team_member={named[1]}",
    ]
    # Printed as well as logged: the migration environment configures no logging.
    for line in lines:
        logger.info(line)
        print(line, file=sys.stderr)


def downgrade() -> None:
    """Drop the columns: approvals bind to the snapshot alone again."""
    for column in (
        "approved_display_name",
        "approved_findings_policy",
        "approved_catalogue_version",
        "approved_rules_version",
        "approved_profile_sha256",
    ):
        op.drop_column(_TABLE, column)
