"""Anchor a stored v2 design's review statuses to the server's decisions (M3).

A template's or connection pattern's review status counts only as far as a
decision recorded on the server supports it, for exactly the definition
stored (its :func:`definition_digest`) and in the revision's tenant or the
global scope. This module is pure: the decisions come in as a
:class:`ReviewAnchor` built by ``app.domain.engineering_reviews``.

:func:`anchor_model` replaces each definition's review, in memory only, with
the anchored one, and refuses a design whose stored review claims anything
else (:class:`ReviewClaimUnmatchedError`). With no decisions, every definition is
unreviewed and any other stored status is refused: M2's rule.

Approved status removes ``template_unreviewed`` / ``pattern_unreviewed``
findings for that definition and nothing more: it never makes a design
ready for execution.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.design import snapshot
from app.design.model import codec_v2, entities

SubjectKind = Literal["template", "pattern", "rule"]

#: The review record of a definition nobody has decided on.
UNREVIEWED = entities.Review(
    status="unreviewed", reviewer=None, reviewed_at=None, standards=(), scope_note=None
)


class ReviewClaimUnmatchedError(ValueError):
    """A stored review that no server-side decision supports."""


def definition_digest(definition: entities.ComponentTemplate | entities.ConnectionPattern) -> str:
    """The digest a decision binds to: the definition with its review unset.

    The canonical JSON of the definition (``codec_v2.dump_template`` or
    ``dump_pattern``) with the review record replaced by :data:`UNREVIEWED`,
    so it names the content reviewed and not the review.

    Args:
        definition: A template or a connection pattern.

    Returns:
        64 lower-case hex digits.
    """
    bare = dataclasses.replace(definition, review=UNREVIEWED)
    if isinstance(bare, entities.ComponentTemplate):
        return snapshot.sha256_of(codec_v2.dump_template(bare))
    return snapshot.sha256_of(codec_v2.dump_pattern(bare))


@dataclass(frozen=True)
class Decided:
    """The current decision for one subject in one scope, as the server recorded it.

    Attributes:
        decision: ``approved``, ``rejected`` or ``withdrawn``.
        scope: ``tenant`` or ``global``.
        record_id: The decision row's id.
        decided_at: When, ISO 8601.
        standards: The standards the reviewer named, as given.
    """

    decision: str
    scope: str
    record_id: str
    decided_at: str
    standards: tuple[str, ...]


@dataclass(frozen=True)
class ReviewAnchor:
    """The current decisions that apply, by ``(kind, ref, digest)``, one per scope."""

    tenant: Mapping[tuple[str, str, str], Decided] = field(default_factory=dict)
    global_: Mapping[tuple[str, str, str], Decided] = field(default_factory=dict)

    def deciding(self, kind: str, ref: str, digest: str) -> Decided | None:
        """The decision that sets the status, or ``None`` for unreviewed.

        A rejection in either scope wins; else an approval in either (the
        tenant's first); a withdrawal or no decision is unreviewed.
        """
        key = (kind, ref, digest)
        found = [d for d in (self.tenant.get(key), self.global_.get(key)) if d is not None]
        for wanted in ("rejected", "approved"):
            for decided in found:
                if decided.decision == wanted:
                    return decided
        return None

    def status(self, kind: str, ref: str, digest: str) -> str:
        """``approved``, ``rejected`` or ``unreviewed``."""
        decided = self.deciding(kind, ref, digest)
        return "unreviewed" if decided is None else decided.decision

    def review(self, kind: str, ref: str, digest: str) -> entities.Review:
        """The anchored review record for a definition."""
        decided = self.deciding(kind, ref, digest)
        if decided is None:
            return UNREVIEWED
        return entities.Review(
            status=decided.decision,
            reviewer=f"server-record:{decided.scope}:{decided.record_id}",
            reviewed_at=decided.decided_at,
            standards=tuple(sorted(decided.standards)),
            scope_note=None,
        )


#: No decisions: every definition unreviewed.
EMPTY = ReviewAnchor()


def anchor_model(model: entities.DesignModelV2, anchor: ReviewAnchor) -> entities.DesignModelV2:
    """The model with every definition's review replaced by the anchored one.

    In memory only: the locks are left as stored, and the result is never
    written back.

    Args:
        model: A stored v2 model as read.
        anchor: The decisions that apply.

    Returns:
        The anchored model (the same object when nothing changes).

    Raises:
        ReviewClaimUnmatchedError: If a stored review is not the unreviewed record
            and differs from the anchored review.
    """
    templates = []
    for template in model.templates:
        review = anchor.review("template", template.ref, definition_digest(template))
        if template.review != UNREVIEWED and template.review != review:
            raise ReviewClaimUnmatchedError(f"template {template.ref}")
        templates.append(dataclasses.replace(template, review=review))
    patterns = []
    for pattern in model.patterns:
        review = anchor.review("pattern", pattern.ref, definition_digest(pattern))
        if pattern.review != UNREVIEWED and pattern.review != review:
            raise ReviewClaimUnmatchedError(f"pattern {pattern.ref}")
        patterns.append(dataclasses.replace(pattern, review=review))
    if tuple(templates) == model.templates and tuple(patterns) == model.patterns:
        return model
    return dataclasses.replace(model, templates=tuple(templates), patterns=tuple(patterns))
