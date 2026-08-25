# ADR 0003: Tenant isolation needs one enforcement point, chosen before the queries exist

- **Status:** Proposed — decide before the first tenant-scoped query ships
- **Date:** 2026-08-25
- **Deciders:** PanelPilot engineering

## Context

BE-002 and BE-014 put multi-tenancy in place: every customer-owned table
carries a non-nullable, indexed `tenant_id`, the tenant is signed into the
access token as `tid`, and `resolve_caller` rejects a token whose tenant no
longer matches the account. That half is done and tested.

The other half is not, and today it cannot be: **nothing forces a query to
filter by tenant.** `TenantScopedMixin` adds a column. It does not add a
`WHERE`. The only consumers — `domain/diagnostics.run_diagnosis` and
`get_session` — are still stubs, so there is nothing to enforce against yet.

This ADR exists because the _next_ thing to happen is the wrong one by
default. When the first real query gets written, the path of least resistance
is `.where(Row.tenant_id == caller.tenant_id)` by hand, in each query, forever.
That is per-query discipline, and it has a specific failure mode:

- It fails **silently**. A forgotten filter does not raise; it returns another
  customer's rows. Tests written by the same person who forgot the filter will
  not catch it, because the fixture only ever has one tenant in it.
- It fails **late**. The first symptom is a customer seeing diagnostics they
  did not run — which for this product means seeing another engineer's fault
  history and equipment inventory.
- It gets **worse with time**. Twenty queries in, retrofitting a central
  mechanism means auditing every one of them. That is precisely the
  "retrofit multi-tenancy later" rewrite BE-002's objective was written to
  avoid, arriving one layer down from where it was expected.

## Decision

**Choose one enforcement mechanism, and install it before the first
tenant-scoped query merges.** The candidates, with the tradeoff that actually
distinguishes them:

1. **SQLAlchemy `with_loader_criteria`**, applied globally via a session event.
   Filters every ORM query on `TenantScopedMixin` subclasses automatically.
   Cheapest to install, invisible at call sites — which is also its weakness:
   raw SQL and `session.execute(text(...))` bypass it entirely.
2. **A mandatory scoped-query helper** — domain code cannot get a `Session`,
   only a `TenantScopedSession` that pre-filters. Enforceable by an
   architecture test that fails if a domain module imports `Session` directly.
   More friction, but the friction is the point, and it is greppable.
3. **Postgres row-level security**, with the tenant set per connection via
   `SET LOCAL`. The only option that also covers raw SQL, migrations, and
   anything connecting outside the ORM. Highest operational cost: every
   connection must set the variable, and forgetting it fails closed (returns
   nothing) rather than open, which is the right direction but noisy.

Whichever is chosen, it must come with an **architecture test** in the shape of
the ones already in `app/tests/test_architecture.py` — the mechanism is only
worth what its guard is worth.

## Consequences

Deciding now costs an afternoon. Deciding after twenty queries exist costs an
audit of all twenty plus the rewrite, and in between, every one of those
queries is a potential cross-tenant disclosure that no test will catch.

Not deciding is itself a decision — for option zero, per-query discipline.

## If you are picking this up

Read `app/models/tables/tenant.py` for what the schema guarantees, and
`app/tests/models/tables/test_tenant.py::test_every_table_is_either_tenant_scoped_or_deliberately_not`
for the list of what is and is not customer data. The enforcement point goes
between those and `app/domain/`.
