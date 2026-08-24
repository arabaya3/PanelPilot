"""Prompt template for evidence-shortfall refusals.

Refusals are generated, not hard-coded, so the engineer is told *what* was
missing and which document would answer their question.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are PanelPilot. The available documentation does not support an answer to
the engineer's question.

Say so in one or two sentences, state precisely what was searched, and name the
documentation that would resolve it. Do not speculate, and do not offer a
partial answer that could be acted on.
"""


def build_refusal_prompt(*, question: str, reason: str) -> str:
    """Render the user-turn prompt for a refusal.

    Args:
        question: The engineer's original question.
        reason: Machine-readable reason from the guardrail, e.g. no passage
            cleared the score floor.

    Returns:
        The formatted user-turn prompt.
    """
    raise NotImplementedError
