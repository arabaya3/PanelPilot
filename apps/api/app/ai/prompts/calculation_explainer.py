"""Prompt template for explaining a completed calculation.

The numbers are already computed by ``app.ai.tools`` before this prompt is
built; the model explains them and must not restate or recompute a value.
"""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are PanelPilot, explaining a completed engineering calculation to the
engineer who requested it.

The values are given and correct. Explain how each was derived, name the
standard or manufacturer clause behind each step, and state the assumptions
that would invalidate the result if wrong. Never alter a supplied number.
"""


def build_calculation_explainer_prompt(*, calculation: dict[str, Any]) -> str:
    """Render the user-turn prompt explaining a calculation result.

    Args:
        calculation: The serialised calculation result, including the source
            citation recorded for each step.

    Returns:
        The formatted user-turn prompt.
    """
    raise NotImplementedError
