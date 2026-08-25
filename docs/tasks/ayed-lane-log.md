# Ayed's lane — unattended run log

One line per task outcome. `MERGED` means the PR is on `main` with CI green
and its checkbox in `ayed-lane.md` is ticked. `FLAGGED` means a PR is open but
deliberately unmerged because a review concern survived two real fix attempts.
`BLOCKED` means the task could not be attempted for a reason outside this
repo's control.

The **Tokens** column is approximate: adversarial review round(s) plus the
implementing agent, so burn rate is visible to anyone checking this log
remotely without reading the full transcript.

| Timestamp (UTC)   | Task   | Outcome | Tokens | Notes                                                                                                                                                                                                                                                                             |
| ----------------- | ------ | ------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-08-25T12:34Z | BE-001 | MERGED  | ~185k  | Aligned Environment to dev/staging/prod, added a fail-loud config path exiting 78 shared by both composition roots, and the boot/health smoke test. 2 review rounds. PR #4.                                                                                                       |
| 2026-08-25T13:01Z | BE-003 | MERGED  | ~330k  | Index mapping plus real hybrid fusion (native hybrid query + normalization-processor). Review proved the first attempt was not actually hybrid — the fixture passed with the kNN leg deleted. Verified-only on production, relaxed for the reviewer path. 3 review rounds. PR #6. |
| 2026-08-25T13:44Z | BE-004 | MERGED  | ~560k  | Implemented promote_chunk with a real audit row, re-crawl overwrite protection, and structural ingestion isolation. First review called the submission "BE-004 documented, not delivered" — the core function was a stub. 4 review rounds. PR #8.                                 |
| 2026-08-25T20:18Z | BE-014 | MERGED  | ~125k  | Added tenants, escalation_items, source_health and tenant-scoped the customer tables. Autogenerate emitted ADD COLUMN NOT NULL, which fails on any populated database — rewritten as nullable-add, backfill, set-not-null. 2 review rounds (deep tier). PR #10.                   |
