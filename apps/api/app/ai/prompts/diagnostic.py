"""Prompt template for diagnostic responses.

One prompt per response type, one file per prompt. Prompts are built by a
function rather than stored as a bare string so that the caller cannot forget
to pass evidence — an un-cited diagnostic prompt should be impossible to
construct.
"""

from __future__ import annotations

from app.models.schemas.diagnostics import DiagnosticRequest
from app.models.schemas.search import RetrievedPassage

SYSTEM_PROMPT = """\
You are PanelPilot, assisting a qualified electrical or control engineer.

Answer only from the supplied evidence passages. Cite the passage id for every
factual claim. If the evidence does not support an answer, say so plainly and
name what documentation would be needed. Never estimate a value that safety
depends on.
"""


def build_diagnostic_prompt(
    *,
    request: DiagnosticRequest,
    evidence: list[RetrievedPassage],
) -> str:
    """Render the user-turn prompt for a diagnostic request.

    Args:
        request: The caller's symptom description and equipment context.
        evidence: Retrieved passages, each rendered with its citable id.

    Returns:
        The formatted user-turn prompt.

    Raises:
        ValueError: If ``evidence`` is empty; refusal is decided by the
            guardrail before a prompt is built, not by the model.
    """
    raise NotImplementedError
