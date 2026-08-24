# ADR 0001: Staging and production are separate indices, with no direct write path from ingestion to production

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** PanelPilot engineering

## Context

PanelPilot answers questions that engineers act on. A wrong ampacity table, a
misparsed derating curve, or a page from a superseded revision of a
manufacturer guide does not produce a bad user experience — it produces a
panel that overheats, a cable that is undersized, or a drive that trips in
service. The engineer asking is qualified, but they are asking precisely
because they do not already know the answer, so they are not in a good
position to catch a plausible-looking error.

Content reaches the system through an automated crawler over manufacturer
documentation sites. That pipeline is inherently unreliable in ways we cannot
fully test away:

- PDFs parse badly. Tables in particular lose their column structure, so a
  value can end up attached to the wrong row.
- Sites reorganise. A crawl can pick up a withdrawn revision that is still
  reachable, or a regional variant with different ratings.
- Chunking changes shift what a citation points at, silently invalidating
  content indexed under previous boundaries.
- A crawl is a bulk operation. One bad parse rule affects thousands of
  documents in a single run.

The obvious design — one index, ingestion writes to it, a `verified` boolean
filters queries — was considered and rejected. See "Alternatives" below.

## Decision

**Two physically separate OpenSearch indices, and exactly one code path
between them.**

1. `OPENSEARCH_STAGING_INDEX` holds everything ingestion produces. Retrieval
   for answer generation never queries it. Only reviewers can search it, and
   only through the review surface.
2. `OPENSEARCH_PRODUCTION_INDEX` holds verified content and is the only index
   that answer generation reads.
3. `app/ingestion/` has no capability to write to production. It does not
   import the production target, and the architecture test
   `test_only_the_promotion_module_writes_production` fails CI if that changes.
4. The single write path into production is
   `app.domain.promotion.promote_document`. It enforces, in code:
   - the actor holds the reviewer role;
   - the reviewer is not the ingester of record for that document
     (four-eyes — one person cannot both bring content in and bless it);
   - automated verification checks have passed;
   - the document carries a resolvable source citation.
5. Promotion writes the production revision and an append-only
   `promotion_audits` row **in the same transaction**. Every live passage
   traces to a named human and a timestamp.
6. Promotion copies; it does not move. Staging keeps its copy, so a
   promotion can be reasoned about after the fact and a bad revision rolled
   back to its predecessor.

## Consequences

**What this buys us**

- No single automated failure can put wrong content in front of an engineer.
  Getting live requires a human decision, recorded.
- The blast radius of a pipeline bug is bounded by staging. A bad crawl is
  cleaned up by reindexing staging, with production untouched.
- "Where did this answer come from, and who approved it?" is answerable from
  one audit table.
- Chunking and embedding changes become safe: re-index staging, re-verify,
  promote. Production is never half-migrated.
- The invariant is mechanically enforced, not merely documented. A new
  engineer cannot violate it accidentally — CI stops them and points here.

**What it costs**

- Roughly double the index storage, and re-embedding on promotion.
- Human review is the throughput ceiling on new content. This is deliberate,
  and it is the cost we chose to pay.
- Two indices to keep in schema sync. Mapping changes must be applied to both,
  staging first.
- Freshness lags publication by the length of the review queue. For
  manufacturer documentation, which changes on a scale of months, that is an
  acceptable trade.

## Alternatives considered

**One index with a `verified` flag, filtered at query time.** Cheaper and
simpler, and rejected because the safety property then rests on every query in
the codebase remembering the filter. One forgotten `WHERE verified = true` —
in a new endpoint, a debugging script, an eval harness — silently serves
unverified content with no signal that anything is wrong. Separate indices make
the failure mode "wrong index name", which is loud and testable.

**Automated verification only, no human in the loop.** We cannot write a
checker for "this table parsed correctly and comes from the current revision"
that we would trust an engineer's safety to. Automated checks are a
precondition for promotion, not a substitute for it.

**Ingestion writes to production behind a feature flag.** Same failure mode as
the `verified` flag, plus a flag someone will eventually flip to unblock a
deploy at 2am.

## If you are changing this

You almost certainly do not need a second write path into production. The
usual real needs are already served:

- *Content needs to go live faster* → shorten the review queue, or add
  automated checks that make review quicker. Not a new path.
- *A bulk correction across many documents* → apply it in staging, re-verify,
  promote in a batch through `promote_document`.
- *An urgent removal* → retraction is a separate, audited operation on
  production. Add it there; do not repurpose ingestion.

If you genuinely believe the invariant should change, supersede this ADR with a
new one rather than editing it, and update
`app/tests/test_architecture.py` in the same PR so the code and the reasoning
move together.
