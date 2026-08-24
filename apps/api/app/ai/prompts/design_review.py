"""Prompt template for design-review responses."""

from __future__ import annotations

from app.models.schemas.calculations import PanelBomResult
from app.models.schemas.search import RetrievedPassage

SYSTEM_PROMPT = """\
You are PanelPilot, reviewing a proposed panel or circuit design for a
qualified engineer.

Comment only on what the supplied design data and evidence passages support.
Cite the passage id behind every standard or manufacturer requirement you
invoke. Flag anything you cannot verify as unverified rather than approving it.
"""


def build_design_review_prompt(
    *,
    design: PanelBomResult,
    evidence: list[RetrievedPassage],
) -> str:
    """Render the user-turn prompt for a design review.

    Args:
        design: The design under review, as produced by the calc tools.
        evidence: Retrieved passages, each rendered with its citable id.

    Returns:
        The formatted user-turn prompt.

    Raises:
        ValueError: If ``evidence`` is empty.
    """
    raise NotImplementedError
