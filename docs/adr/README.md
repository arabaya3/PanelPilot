# Architecture Decision Records

One file per significant decision, numbered in order: `NNNN-short-title.md`.

Write an ADR when a decision would be expensive to reverse, constrains future
work, or will look arbitrary to someone who wasn't in the room. Skip it for
choices a reader can infer from the code.

Record the decision *and the alternatives you rejected* — the rejected options
are what stop the discussion being reopened every six months.

ADRs are immutable once accepted. If a decision changes, add a new ADR that
supersedes the old one and mark the old one `Superseded by NNNN`. Never edit
history.

Template:

```markdown
# ADR NNNN: Title

- **Status:** Proposed | Accepted | Superseded by NNNN
- **Date:** YYYY-MM-DD
- **Deciders:**

## Context
What forces are at play? What makes this hard?

## Decision
What we are doing, stated so it can be checked.

## Consequences
What this buys us, and what it costs.

## Alternatives considered
What we rejected, and why.
```

## Index

- [0001 — Staging vs production index separation](0001-staging-vs-production-index.md)
- [0002 — One package, two runtimes; AI is a layer, not a service](0002-one-package-two-runtimes.md)
