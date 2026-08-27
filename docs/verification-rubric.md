# Verification labelling rubric

The rule for applying `correct`, `incorrect`, and `uncertain` to a staged chunk.

Ten engineers label the same corpus. If they read these words differently, the
pipeline's output quality varies by who drew a given item — which is the ad-hoc
spot-checking this process replaces. This document exists so the labels mean the
same thing in ten pairs of hands, and so disagreement is a fixable defect in the
rubric rather than an unresolvable difference of opinion.

It is deliberately concrete. "This looks about right" is not a verification.

## What you are checking

A **chunk**: a passage of manufacturer documentation with a citation attached —
document, section, page. You are not judging whether the content is good
engineering practice. You are judging **one question**:

> Does the cited location actually say this?

That is narrower than it sounds, and the narrowness is the point. An engineer
downstream will be shown this content _with that citation_ as grounds for a
decision. If the citation does not support the content, the claim is unfounded
no matter how sensible it reads.

## The labels

### `correct`

Apply when **all** of the following hold:

1. **The cited location exists.** The document, section, and page are real and
   the section is where the citation says it is.
2. **The cited location states the content.** Not implies it, not is consistent
   with it — states it. A value present in the chunk must appear at the cited
   location, or follow from a formula stated there.
3. **Any method matches the source's method.** If the chunk gives a calculation,
   the formula, the coefficients, and the conditions of application are the
   source's, not a reasonable equivalent.
4. **Nothing material is lost by the chunk boundary.** A parameter table cut in
   half presents partial data as complete. A procedure starting at step 4 reads
   as if it starts at the beginning. If the boundary changes the meaning, this
   is not `correct` — see the note on atomic blocks below.

If you find yourself constructing an argument for why the content is
_defensible_, stop. `correct` means the source says it.

### `incorrect`

Apply when the source **contradicts** the content, or when the citation does not
support it. Both are `incorrect`, and the note should say which:

- **Contradiction.** The source states 63 A; the chunk says 80 A.
- **Unsupported citation.** The cited section is real but says nothing about
  this. The claim may even be true elsewhere in the document — it is still
  `incorrect` here, because the citation is the evidence, and this one does not
  hold it up.
- **Broken citation.** The section or page does not exist.

An unsupported citation is the more dangerous of the two and the easier to miss:
plausible content with an authoritative-looking reference that does not check
out. Read the cited location before deciding, every time.

### `uncertain`

Apply when you **cannot apply the rubric confidently**. This is a first-class
outcome, not an admission of failure.

Reach for it when:

- The source is ambiguous, or two passages appear to conflict.
- The content depends on context outside the chunk that you cannot see.
- The cited location is in a language, notation, or domain you cannot read well
  enough to judge.
- You have read it twice and still are not sure.

**Do not guess.** An engineer forced to choose between `correct` and `incorrect`
on an item they cannot judge will sometimes guess, and a guessed `correct` is
indistinguishable from a verified one afterwards. That is the failure this label
prevents. Nobody is measured on how few items they mark `uncertain`.

## What happens next

`correct` closes the item.

`incorrect` and `uncertain` both **route to lead-engineer review**. You do not
resolve them yourself, and you do not need to be right about which of the two it
is — a lead will look at either. The rule is encoded in
`app/models/schemas/verification.py` so it cannot drift from this document
silently.

The two escalate for different reasons, which is why they stay separate labels:

- `incorrect` means wrong content reached staging. Often a crawler or chunking
  defect sits behind it, so the fix is usually upstream of the chunk.
- `uncertain` means **the rubric did not settle the case**. That is a defect in
  this document. Escalations of this kind are read back into revising it — see
  below.

## When a `correct` label turns out to be wrong

It happens, and it is not treated as one person's mistake to correct quietly.

A chunk labelled `correct` that later proves wrong means either the rubric
permitted it or the training did — both fixable, neither fixed by silently
relabelling the item. The correction goes back into this document: a new
worked example, a sharpened criterion, a case added to the calibration set.

The same applies to a cluster of `uncertain` labels on similar items. Several
verifiers unable to judge the same _kind_ of chunk is a specific, addressable
gap in the rubric.

## Atomic blocks

Some content must not be split, and a chunk that splits it is `incorrect`
regardless of its citation:

- **Parameter tables.** Half a table reads as a whole one. A verifier seeing
  rows 1-6 of a 12-row rating table has no way to know rows 7-12 exist.
- **Procedures.** Steps 4-9 of a nine-step procedure read as a complete
  procedure starting at step 4.
- **A value and its conditions.** A rating separated from the ambient
  temperature it assumes is not a rating, it is a number.

If the boundary changes what the content means, that is the finding — label it
`incorrect` and say so in the note.

## Calibration

Before joining the rota, two verifiers independently label the same ten items
from the calibration set and compare. Disagreement on any item is discussed
against this document, and **the rubric is revised** where it failed to settle
the case — the point of the exercise is to find where this document is unclear,
not to find which of the two verifiers is better.

The rubric is not considered ready to roll out until two verifiers agree
independently on all ten.

Calibration is repeated when the rubric changes materially, and periodically
thereafter — agreement drifts as people develop private conventions, and the
drift is invisible until it is measured.

## Writing the note

Every `incorrect` and `uncertain` label carries a note. A lead should be able to
act on it without re-deriving your reasoning.

Useful:

> Cited section 4.2.1 gives the 40 °C rating as 63 A. Chunk says 80 A, which is
> the 30 °C figure from the row above.

> Table is cut after row 6; the source's table continues to row 12 on the
> following page.

Not useful:

> Doesn't look right.

> Wrong value.
