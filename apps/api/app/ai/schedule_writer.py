"""The model as the drafter of a load schedule -- never as its designer.

An engineer describes what a board feeds in words; this asks the configured
model, through a forced tool call, for the groups of points it names: how
many, of what, at what power each, and where that power came from. Splitting
points into circuits is left to the company's rule (``schedule_split``): a
model asked to group them split them inconsistently in live tests.

What comes back is a proposal. Nothing here sizes a breaker or a cable: the
rows go to the same table an engineer types into, the assumptions are shown
beside them, and the sourced design runs only when the engineer submits the
table. That review is this module's gate, which is why the architecture test
names it rather than the cite-or-refuse gate, which has no evidence to judge
here.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai.anthropic_client import get_llm_client
from app.ai.structured_output import extract_named_tool_payload, input_schema_for
from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError
from app.core.errors import ValidationError as InputError
from app.models.schemas.design import ScheduleSuggestionOutput, ScheduleSuggestionRequest

#: The forced tool.
TOOL_NAME = "emit_load_schedule"

#: Output ceiling: sixty circuits with their assumptions fit comfortably.
MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = """You read the electrical loads out of an engineer's description.

Return each group of like points once, with how many there are and the power
of one: "20 sockets" is one item of quantity 20. Do not split points into
circuits; that is done afterwards by the company's rules.

- Language: write in the language the request names.
- An item's description names the kind and place, never the count:
  "Hall sockets", "High-bay lights", "مآخذ القاعة"; not "20 sockets".
- An item's assumption says only where its power came from: "given" or
  "typical 150 W per socket". Overall assumptions go in the overall list.
- Power of one point: use what the description gives. Where it gives none,
  use a typical figure and write "typical" in the assumption. Typical:
  socket point 150 W; LED luminaire 30 W; LED high-bay 150 W; 1-ton split
  air conditioner 1.2 kW electrical, 2-ton 2.5 kW, 3-ton 3.5 kW; kitchen
  exhaust fan 250 W; water heater 2 kW.
- three_phase is true only for a load three-phase by nature: a motor or air
  conditioner over 5 kW, a lift, a chiller, a three-phase cooker.
- Load kinds, only these: lighting, socket, air_conditioning, water_heater,
  kitchen, fan, motor, lift, sub_board, control, data, other.
- Overall assumptions: say diversity is not applied and spares are not
  included, in the description's language.
- Never invent loads the description does not imply.
"""


def description_language(text: str) -> str:
    """Name the language a description is written in, for the model to answer in.

    Told rather than inferred: asked to "answer in the description's
    language", the model answered an English description in Arabic in live
    tests, after an Arabic one.

    Args:
        text: The engineer's description.

    Returns:
        "Arabic" when Arabic letters outnumber Latin ones, else "English".
    """
    arabic = sum(1 for ch in text if "\u0600" <= ch <= "\u06ff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return "Arabic" if arabic > latin else "English"


def _tool() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Return each group of points and every assumption behind them.",
        "input_schema": input_schema_for(ScheduleSuggestionOutput),
    }


def write_schedule(
    request: ScheduleSuggestionRequest, *, client: Any = None
) -> ScheduleSuggestionOutput:
    """Ask the model for a proposed load schedule.

    Args:
        request: The description and the supply.
        client: Injected for tests; the configured model by default.

    Returns:
        The proposed circuits and assumptions.

    Raises:
        InputError: If the model returned nothing usable, or was cut off.
        ServiceUnavailableError: If the model could not be reached.
    """
    resolved = client if client is not None else get_llm_client()
    supply = "three-phase" if request.supply_phases == 3 else "single-phase"
    language = description_language(request.description)
    try:
        message = resolved.messages.create(
            model=get_settings().generation_model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Supply: {supply}.\nWrite descriptions and assumptions in "
                        f"{language}.\n\nDescription:\n{request.description}"
                    ),
                }
            ],
            tools=[_tool()],
            tool_choice={"type": "tool", "name": TOOL_NAME},
        )
    except Exception as exc:
        # Broad: each provider client raises its own types, and every one of
        # them means the same thing to the engineer -- try again later.
        raise ServiceUnavailableError("the schedule could not be drafted; try again") from exc
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise InputError("the schedule was cut off at the output limit; describe less at once")
    payload = extract_named_tool_payload(message, TOOL_NAME)
    if payload is None:
        raise InputError("no schedule came back; describe the loads more concretely")
    try:
        return ScheduleSuggestionOutput.model_validate(payload)
    except ValidationError as exc:
        raise InputError("the drafted schedule did not fit the table; try rephrasing") from exc
