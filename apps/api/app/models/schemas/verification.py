"""The verification labelling vocabulary, and the rule that routes escalations.

Ten engineers apply these labels to the same corpus. If they interpret them
differently the pipeline's output quality varies by who happened to draw a
given item, which is precisely the ad-hoc spot-checking this replaces. So the
vocabulary is small, the rubric that defines it is a reviewable document
(``docs/verification-rubric.md``) rather than tribal knowledge, and the
routing rule lives here as code rather than as a convention people remember.

The three labels are deliberately not a quality scale. ``UNCERTAIN`` is not
"somewhat correct" — it is a verifier declining to decide, which is a
different act with a different destination. Collapsing it into ``INCORRECT``
would lose the distinction between "the source contradicts this" and "I could
not tell", and those need different people looking at them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class VerificationLabel(StrEnum):
    """A verifier's judgement on one chunk.

    Deliberately three values, not a numeric confidence. A scale invites
    averaging, and there is no meaningful average of "the source says 63 A"
    and "the source does not mention this" — the second is a citation failure
    whatever the first says.
    """

    #: The cited section states this, and any method matches the source's.
    #: The rubric defines what must be checked; "looks about right" is not it.
    CORRECT = "correct"

    #: The source contradicts the content, or the citation does not support it.
    INCORRECT = "incorrect"

    #: The verifier could not apply the rubric confidently. A first-class
    #: outcome, not a failure to do the job: an engineer forced to choose
    #: between "correct" and "incorrect" on an item they cannot judge will
    #: sometimes guess, and a guessed "correct" is indistinguishable from a
    #: verified one after the fact. This label is what makes that unnecessary.
    UNCERTAIN = "uncertain"


#: Labels that route to lead-engineer review instead of closing the item.
#:
#: Both non-correct labels escalate, for different reasons. ``INCORRECT`` means
#: content that reached staging is wrong, which is a corpus problem and often a
#: crawler or chunking problem behind it. ``UNCERTAIN`` means the rubric did not
#: settle the case, which is a rubric problem — and per AI-012's edge case,
#: those feed back into refining the rubric rather than being resolved once and
#: forgotten.
ESCALATING_LABELS = frozenset({VerificationLabel.INCORRECT, VerificationLabel.UNCERTAIN})


def escalates(label: VerificationLabel) -> bool:
    """Report whether a label routes to lead-engineer review.

    Args:
        label: The verifier's judgement.

    Returns:
        ``True`` when the item must go to a lead rather than close.

    A function rather than a bare set membership at each call site, so the
    routing rule has one definition. AI-012 makes this a hard requirement:
    an incorrect or uncertain label must never be resolved unilaterally by
    the verifier who applied it.
    """
    return label in ESCALATING_LABELS


class QueueItem(BaseModel):
    """One chunk in a verifier's queue."""

    id: UUID
    chunk_id: str | None
    status: str
    assigned_at: datetime | None


class QueuePage(BaseModel):
    """A verifier's outstanding batch."""

    items: list[QueueItem]


class LabelRequest(BaseModel):
    """A verifier's judgement on one item."""

    label: VerificationLabel
    # Required in practice for anything that escalates; the domain refuses an
    # empty note rather than the schema, so the message names the rule.
    note: str = ""


class LabelResponse(BaseModel):
    """The outcome of recording a label."""

    id: UUID
    status: str
    label: str | None


class EscalationPage(BaseModel):
    """Items awaiting lead-engineer review."""

    items: list[QueueItem]
