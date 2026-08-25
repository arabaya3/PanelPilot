"""Tests for `app/ai/guardrails/cite_or_refuse.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

This is the cite-or-refuse invariant: PanelPilot answers from cited
documentation or it declines. The functions are still stubs, so these pin the
signatures and current behaviour and will fail the moment either is
implemented — at which point they must be replaced by tests that assert the
actual refusal behaviour, including the empty-evidence and
citation-does-not-resolve cases named in the docstrings.
"""

from __future__ import annotations

import pytest

from app.ai.guardrails import cite_or_refuse
from app.models.schemas.diagnostics import GeneratedAnswer
from app.models.schemas.search import Citation, RetrievedPassage


def _passage() -> RetrievedPassage:
    return RetrievedPassage(
        id="doc-1#3",
        text="Ambient correction factors are given in Table B.52.14.",
        score=0.82,
        citation=Citation(
            document_id="doc-1",
            document_title="Electrical Installation Guide 2024",
            manufacturer="Schneider Electric",
            page=412,
            section="G.6",
        ),
    )


def test_require_evidence_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        cite_or_refuse.require_evidence([_passage()], min_score=0.35)


def test_require_evidence_rejects_nothing_silently_on_empty_input() -> None:
    """Empty evidence must not quietly return an empty list once implemented.

    Pinned now so the refusal path cannot regress into a silent success.
    """
    with pytest.raises(NotImplementedError):
        cite_or_refuse.require_evidence([])


def test_verify_citations_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        cite_or_refuse.verify_citations(
            GeneratedAnswer(text="Use 16 mm2.", cited_passage_ids=["doc-1#3"]),
            evidence=[_passage()],
        )
