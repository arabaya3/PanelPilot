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
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.models.schemas.search import RetrievedPassage


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


class FlaggedAnswerView(BaseModel):
    """An answer an engineer reported as wrong, as they saw it.

    ``passages`` is ``None`` when the stored context cannot be read, which is
    not the same as an answer built on nothing and must not be shown as one.
    """

    question: str
    answer: str
    reason: str | None
    passages: list[RetrievedPassage] | None
    flagged_at: datetime


class QueueItem(BaseModel):
    """One item in a verifier's queue, with what the verifier checks it against.

    A crawled chunk (``origin`` ``crawl``): ``content`` is its text and
    ``source_url``/``page``/``section`` say where it came from, all ``None``
    when the staged chunk cannot be read; the console then says so rather than
    inviting a label on unseen text.

    A reported answer (``origin`` ``user-flag``): ``flag`` carries the
    question, the answer, the reporter's reason and the passages behind it.
    """

    id: UUID
    chunk_id: str | None
    status: str
    assigned_at: datetime | None
    content: str | None = None
    source_url: str | None = None
    page: int | None = None
    section: str | None = None
    origin: str = "crawl"
    flag: FlaggedAnswerView | None = None
    # The verifier's label and note. Shown on an escalation, where they are
    # what the lead is resolving.
    label: str | None = None
    note: str | None = None
    # Whether the caller is who the item is assigned to -- on an escalation,
    # who raised it, and so who may not resolve it.
    assigned_to_you: bool = False


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


class StaleDocument(BaseModel):
    """A live document whose source now serves something other than what was verified.

    ``upstream_hash`` is ``None`` when the source withdrew it. The review
    fields are set only on a dismissed flag.
    """

    id: UUID
    source_url: str
    source_id: str
    reason: str
    status: str
    published_hashes: list[str]
    upstream_hash: str | None
    first_flagged_at: datetime
    last_checked_at: datetime
    reviewed_at: datetime | None = None
    review_note: str | None = None


class StaleDocumentPage(BaseModel):
    """Stale-document flags in one status."""

    items: list[StaleDocument]


class DismissStaleRequest(BaseModel):
    """A reviewer's reason for dismissing a stale-document flag."""

    # Required in practice; the domain refuses a blank one so the message
    # names the rule, as for an escalating label's note.
    note: str = ""


class ResolveEscalationRequest(BaseModel):
    """A lead's resolution of one escalated item.

    ``upheld``: the escalation stands. ``taken-over``: the lead judges the
    item again themselves, from their own queue.
    """

    outcome: Literal["upheld", "taken-over"]
    note: str
