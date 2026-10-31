-- Read-only counts of the approvals that must be granted again under the
-- renderer version (docs/design-readiness/phase-1-export-integrity.md,
-- "Legacy approvals: re-approval runbook"). Counts only: no ids, names or
-- content. Runs inside a READ ONLY transaction, so it cannot change data.
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/legacy-approval-counts.sql
--
-- Works on any schema from main's (no snapshot columns) to head: columns a
-- schema lacks are probed through to_jsonb, which reads them as NULL.
BEGIN TRANSACTION READ ONLY;

SELECT
  count(*) FILTER (WHERE approved_at IS NOT NULL) AS approved_revisions,
  -- Approved before Phase 1: no stored design, so never bound; saved again anyway.
  count(*) FILTER (WHERE approved_at IS NOT NULL
                   AND to_jsonb(r) ->> 'approved_snapshot_sha256' IS NULL) AS approved_unbound,
  -- Bound to a design, but with no renderer recorded: re-approval needed.
  count(*) FILTER (WHERE approved_at IS NOT NULL
                   AND to_jsonb(r) ->> 'approved_snapshot_sha256' IS NOT NULL
                   AND to_jsonb(r) ->> 'approved_renderer_version' IS NULL) AS approved_no_renderer,
  count(*) FILTER (WHERE to_jsonb(r) ->> 'approved_renderer_version' IS NOT NULL) AS approved_with_renderer,
  count(DISTINCT project_id) FILTER (WHERE approved_at IS NOT NULL
                   AND to_jsonb(r) ->> 'approved_renderer_version' IS NULL) AS projects_affected,
  count(DISTINCT tenant_id) FILTER (WHERE approved_at IS NOT NULL
                   AND to_jsonb(r) ->> 'approved_renderer_version' IS NULL) AS tenants_affected
FROM design_project_revisions AS r;

-- Outstanding work: projects whose newest approved revision still has no
-- renderer recorded. Recovered projects drop out; their old revisions stay
-- in the history above by design.
SELECT count(*) AS projects_outstanding
FROM (
  SELECT DISTINCT ON (project_id) project_id,
         to_jsonb(r) ->> 'approved_renderer_version' AS renderer
  FROM design_project_revisions AS r
  WHERE approved_at IS NOT NULL
  ORDER BY project_id, number DESC
) AS newest
WHERE renderer IS NULL;

ROLLBACK;
