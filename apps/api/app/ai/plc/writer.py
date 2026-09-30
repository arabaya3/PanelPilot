"""The model as the writer of PLC code -- and never as its judge.

``generation.generate_plc_code`` takes a writer and a validator and will not
return code without the validator's verdict. This module is the writer: it
asks the configured model for Structured Text or ladder rungs through a forced
tool call, so the answer arrives as a field or as structured rungs rather than
as prose with code somewhere inside it.

It has no retrieved evidence to cite, so the cite-or-refuse gate has nothing
to say about it; the parser-based validator is its gate, and the architecture
test names this module for that reason.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.ai.anthropic_client import get_llm_client
from app.ai.plc.generation import GenerationError
from app.ai.structured_output import extract_named_tool_payload
from app.core.config import get_settings
from app.models.schemas.plc import LadderRung, PlcDialect, PlcGenerationRequest

#: The forced tool for Structured Text.
SOURCE_TOOL_NAME = "emit_structured_text"

#: The forced tool for ladder.
LADDER_TOOL_NAME = "emit_ladder"

#: Output ceiling. A generated program is a page or two; a request that needs
#: more is better refused at the limit than returned cut off.
MAX_OUTPUT_TOKENS = 4096

#: How each dialect is named to the model.
_DIALECT_NAMES = {
    PlcDialect.IEC_61131_3: "IEC 61131-3 Structured Text",
    PlcDialect.SIEMENS_SCL: "Siemens SCL (TIA Portal)",
    PlcDialect.ROCKWELL_ST: "Rockwell Studio 5000 Structured Text",
    PlcDialect.CODESYS_ST: "CODESYS Structured Text",
}

_RUNGS = TypeAdapter(list[LadderRung])

SYSTEM_PROMPT = """You write PLC programs for industrial control engineers.

Rules:
- Write exactly what the description asks for, no more. Do not invent I/O the
  description does not imply; name every tag clearly.
- Declare every variable you use.
- Stops, emergency stops and interlocks are normally-closed and fail safe:
  losing the signal must stop the output.
- Keep one program unit. No explanation outside the tool call; comments in
  the code are welcome.
"""


def _source_tool() -> dict[str, Any]:
    return {
        "name": SOURCE_TOOL_NAME,
        "description": "Return the complete program.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The complete program, with its declarations.",
                }
            },
            "required": ["source"],
        },
    }


def _ladder_tool() -> dict[str, Any]:
    return {
        "name": LADDER_TOOL_NAME,
        "description": (
            "Return the rungs, left to right. Contacts are kind 'no' or 'nc'; "
            "the output is kind 'coil'. A parallel branch is a list of paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"rungs": _RUNGS.json_schema()},
            "required": ["rungs"],
        },
    }


def _ask(request: PlcGenerationRequest, tool: dict[str, Any], client: Any) -> dict[str, Any]:
    """Ask for one forced tool call, refusing a cut-off or absent answer.

    Args:
        request: What to generate.
        tool: The tool to force.
        client: The model client; the configured one by default.

    Returns:
        The tool call's input.

    Raises:
        GenerationError: If the answer was cut off or never came.
    """
    resolved = client if client is not None else get_llm_client()
    message = resolved.messages.create(
        model=get_settings().generation_model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Target: {_DIALECT_NAMES[request.dialect]}.\n\n"
                    f"Description:\n{request.description}"
                ),
            }
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
    )
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise GenerationError("the program was cut off at the output limit; narrow the request")
    payload = extract_named_tool_payload(message, tool["name"])
    if payload is None:
        raise GenerationError("the model returned no program")
    return payload


def write_source(request: PlcGenerationRequest, *, client: Any = None) -> str:
    """Write Structured Text for a request.

    Args:
        request: What to generate.
        client: Injected for tests; the configured model by default.

    Returns:
        The program's source.

    Raises:
        GenerationError: If no usable program came back.
    """
    source = _ask(request, _source_tool(), client).get("source")
    if not isinstance(source, str) or not source.strip():
        raise GenerationError("the model returned an empty program")
    return source


def write_ladder(request: PlcGenerationRequest, *, client: Any = None) -> list[LadderRung]:
    """Write ladder rungs for a request.

    Args:
        request: What to generate.
        client: Injected for tests; the configured model by default.

    Returns:
        The rungs.

    Raises:
        GenerationError: If no usable rungs came back.
    """
    try:
        rungs = _RUNGS.validate_python(_ask(request, _ladder_tool(), client).get("rungs"))
    except ValidationError as exc:
        raise GenerationError("the model returned rungs that do not fit the ladder schema") from exc
    if not rungs:
        raise GenerationError("the model returned no rungs")
    return rungs
