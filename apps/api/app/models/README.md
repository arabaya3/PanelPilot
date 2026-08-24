# models/

Two kinds of type live here and they are kept in separate packages on purpose.

| Package | Contains | Rule |
| --- | --- | --- |
| `tables/` | SQLAlchemy ORM models — the database shape | Never returned from a domain function, never serialised to a client |
| `schemas/` | Pydantic models — request, response, and internal transfer shapes | Never used as a query target |

Why the split: the persisted shape and the wire shape change for different
reasons and on different schedules. Collapsing them means a column rename
becomes an API break, and an API field addition tempts someone into a migration
they did not need.

Conversion happens in `app/domain/` — a domain function takes schemas in and
returns schemas out, touching `tables/` only in between.

Schema changes to `tables/` are applied through Alembic migrations only. Never
edit a live schema by hand.
