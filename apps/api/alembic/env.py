"""Alembic environment.

Pulls the database URL and metadata from the application rather than from
``alembic.ini``, so migrations always run against the same configuration the
app uses. Every schema change ships as a migration — there is no supported
path for editing a live schema by hand.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.models.tables import base

# Importing the table modules registers them on the metadata Alembic compares
# against. A new table module must be imported here or autogenerate misses it.
from app.models.tables import calculations, diagnostics, ingestion, user  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.get_secret_value())
target_metadata = base.Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL for the migration without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migration against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
