# ADR 0004: A voltage-drop calculation record, stored and bound like the requirements record

- **Status:** Proposed
- **Date:** 2026-11-21
- **Deciders:** PanelPilot engineering

## Context

A saved revision stores the design the server built (`design_snapshot`), its
hash, and the rules, catalogue and profile it was built under. For voltage
drop it stores only each circuit's rounded percentage on its own cable
(`Circuit.voltage_drop_percent`) and, in note text, some totals. What the
software actually read to get there is not stored: the design current and
voltage it used, which table and column, the cells, the sections it tried, why
the cable ended at its final size, the limit and where that limit came from,
and what the feeders upstream contributed. A reviewer cannot trace a printed
"dU %" back to the execution that produced it.

Forces:

- **History is immutable.** Snapshots, their hashes, findings and approvals of
  saved revisions must never be rewritten or backfilled. A record computed
  today for an old revision would describe today's rules, not the save.
- **The snapshot is hashed and versioned.** Adding the trace to the snapshot
  would change every new snapshot's content and couple it to the snapshot
  schema and renderers.
- **Approval binds evidence.** If a reviewer may rely on the record, an
  approval must bind the exact record it was granted with, or the record could
  change under an approval. The requirements record (`pp-req/1`) already
  solves this: its own JSONB column, its own SHA-256 checked on every read, and
  `approved_requirements_sha256` set in the approval's locked transaction.
- **One engine.** A trace rebuilt by a second, independent calculation would
  be a claim about what the engine *should* do, not evidence of what it did.
- **It is not engineering validation.** The record says what the software
  computed and from which recorded sources. It does not say the method,
  tables or limits are right; several are open engineering questions.

## Decision

1. **A separate record per revision, beside the snapshot.** New nullable
   columns on `design_revisions`:
   - `calculation_record` (JSONB): the record, schema marker `pp-vdrec/1`;
   - `calculation_record_sha256`: SHA-256 of its canonical JSON
     (`app.design.snapshot.canonical_json`, the snapshot's own encoding);
   - `approved_calculation_record_sha256`: set with an approval.
   The snapshot, its schema and its hash are unchanged.

2. **Written once, atomically, with the revision.** The record is built from
   the same design call that builds the snapshot (`_validate`) and returned in
   the same column set, so it is inserted in the same row and transaction as
   the snapshot. Nothing updates it afterwards. A design that is refused has no
   snapshot and no record.

3. **Captured during the calculation.** The voltage-drop functions expose the
   readings they already make (table, column, cell, volts) through one code
   path that the unrecorded functions also use, so a traced and an untraced
   design compute the same numbers. The distribution-board design records each
   circuit's inputs, current, voltage, the sections tried and chosen, every
   later enlargement and its reason, the final (settled) drop, the limit used
   and the upstream contribution. Only the project's final, budgeted pass is
   recorded (its first pass sizes feeders and is not what is saved).

4. **Bound to the revision.** The record states the `snapshot_sha256`,
   `profile_sha256` and `rules_version` of the revision it was written with.
   It *matches* only when its hash equals `calculation_record_sha256`, its
   shape validates, and its `snapshot_sha256` equals the row's own. The
   profile hash and rules version it states are information: the row's own
   are already held by the stale checks, which report a change of either as
   `revision_stale` rather than as an altered record. A record that does not
   match is an integrity failure (`snapshot_integrity_failed`, reason
   `calculation_record_mismatch`), refused for approval and export exactly as
   a requirements mismatch is.

5. **Bound to new approvals.** An approval records
   `approved_calculation_record_sha256` = the record's hash (or the hash of
   JSON `null` for a revision with no record), under the approval's existing
   locks. The approval stops counting (`approval_changed: calculation_record`)
   if the bound hash differs from the row's binding, or the record no longer
   matches.

6. **Historical compatibility.**
   - A revision saved before this change has no record. It reads as
     **not recorded**; nothing is derived or backfilled.
   - Unlike the requirements binding, an approval with no recorded binding is
     read as one made before records were bound, and is not checked against a
     record: a missing historical record alone never invalidates an approval.
     Every approval this software writes records the binding. (Treating an
     unbound approval on a recorded row as not counting was considered; it adds
     nothing against someone able to write approval columns, who could write
     the binding as well, and it misreads rows prepared as legacy approvals.)
   - Snapshots, hashes, findings and exports of existing revisions are
     unchanged. PDFs and other exports do not print the record in this slice.

7. **Honest unknowns.** The record states what the software does not know:
   a table edition not recorded in the code is `unknown`; a limit's origin is
   the resolved company profile key (defaults, market settings and company
   settings are not distinguished there); an upstream feeder whose drop was not
   checked contributes `unknown` (the software counts it as 0), and every total
   below it is marked as not complete. The drop before the project's first
   board is not modelled. The record carries a fixed statement that it is
   evidence of software execution, not engineering validation.

8. **Read through the API** for the revision's tenant only, as the revision
   itself is (`GET …/revisions/{n}/calculation-record`), returning
   `recorded` with the record and its hash, or `not_recorded`.

## Alternatives rejected

- **Inside the snapshot.** Changes every new snapshot hash and content, ties
  the trace to the snapshot schema versions and renderer fingerprints, and
  would make "keep exports unchanged" impossible to guarantee simply.
- **A separate table, one row per circuit.** Approval locks the project and
  revision rows; side-table rows would not be covered by the approval binding
  unless also hashed into it, which is this decision with more moving parts.
  No query needs records outside their revision.
- **Recompute on read.** A record computed when it is read describes the
  current rules, not the save; it is a second calculation, not evidence.
- **Backfill old revisions.** Same objection, applied to history.
- **Bind like requirements exactly (NULL binding = not current).** That would
  invalidate every existing approval on deploy, for a record those approvals
  never had.

## Consequences

- One Alembic migration adds three nullable columns; rollback drops them. Code
  rolled back while records exist simply ignores them; approvals made with a
  binding keep counting under the older code, which does not check it.
- Every read of a revision checks one more hash.
- The record format is versioned (`pp-vdrec/1`); a later change to what is
  traced adds a new marker rather than reinterpreting old records.
- A rules change that alters drops already makes old revisions stale; their
  records stay as written and describe the rules they were saved under.
