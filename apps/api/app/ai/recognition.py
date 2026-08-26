"""Reading a fault code off a photo of an equipment display.

Removes a specific, real error source: a stressed engineer squinting at a
small glare-affected screen and mistyping the code into the chat box.

**Narrow extraction, not description.** The model is constrained to exactly
three fields plus a verdict. "Describe this image" would produce prose that
something downstream would then have to parse, which is the failure AI-004
exists to prevent, one layer earlier.

**Confidence gates each field independently.** A photo often shows the code
clearly and the manufacturer not at all. Gating the whole result on one number
would either discard a readable code because no logo was in frame, or accept a
guessed brand because the code was sharp.

**A guess is worse than a refusal here.** A fabricated code does not look
wrong: it looks like a code, and it sends an engineer to a real procedure for
a fault they do not have. So the verdict is checked before any field is read,
and a low-confidence code becomes a confirm-back rather than an answer.
"""

from __future__ import annotations

import base64
from typing import Any

from pydantic import ValidationError

from app.ai.structured_output import extract_named_tool_payload, input_schema_for
from app.core.errors import ValidationError as DomainValidationError
from app.models.schemas.images import ImageFormat
from app.models.schemas.recognition import (
    DisplayVerdict,
    FaultRecognitionResult,
    RecognisedField,
)

RECOGNITION_TOOL_NAME = "report_display"

# Below this a field is not used without asking the engineer to confirm it.
# Deliberately high: the cost of a wrong code is a wasted call-out or a
# procedure carried out on the wrong fault, and the cost of asking is one tap.
MIN_FIELD_CONFIDENCE = 0.8

SYSTEM_PROMPT = """\
You are reading a photograph taken by an electrical engineer, who believes it
shows a fault or alarm display on industrial equipment.

Report only what is legibly visible. Transcribe characters exactly as shown,
including leading zeros and separators — do not normalise, expand or correct
what you read.

If the photograph does not show a fault or alarm display, say so and report no
fault code. If it shows one you cannot read — glare, blur, angle, a screen
that is off — say that instead, which is a different problem for the engineer
to fix.

A code you are unsure of is worth far less than saying you are unsure. An
invented code looks exactly like a real one and will send someone to the wrong
procedure.
"""

TOOL_DESCRIPTION = (
    "Report what is visible in the photograph: whether it is a readable fault "
    "display, and the fault code, brand and model if legibly shown."
)


def recognition_tool_definition() -> dict[str, Any]:
    """Return the tool constraining the model's report.

    Returns:
        A tool definition in the shape the Claude API expects, derived from
        ``FaultRecognitionResult`` so the constraint and the type cannot
        disagree.
    """
    return {
        "name": RECOGNITION_TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": input_schema_for(FaultRecognitionResult),
    }


def image_block(data: bytes, image_format: ImageFormat) -> dict[str, Any]:
    """Render image bytes as a content block for the API.

    Args:
        data: The image.
        image_format: What the bytes are, as sniffed by BE-009 — never as the
            uploader declared them.

    Returns:
        A content block carrying the base64-encoded image.
    """
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image_format.media_type,
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


def parse_recognition(payload: dict[str, Any]) -> FaultRecognitionResult:
    """Validate a tool-call payload into a recognition result.

    Args:
        payload: The raw ``input`` from the model's tool call.

    Returns:
        The validated result.

    Raises:
        DomainValidationError: If the payload does not match the schema. Not
            repaired and not partially read: a report the system could not
            parse is one it cannot act on, and half a report about a fault
            code is worse than none.
    """
    try:
        return FaultRecognitionResult.model_validate(payload)
    except ValidationError as exc:
        raise DomainValidationError(f"the image report could not be validated: {exc}") from exc


def recognise_fault_display(
    client: Any,
    *,
    model: str,
    data: bytes,
    image_format: ImageFormat,
    max_tokens: int = 512,
) -> FaultRecognitionResult:
    """Read a fault display from a photograph.

    Args:
        client: An Anthropic client.
        model: Vision-capable model id.
        data: The image bytes, from ``images_domain.get_image``.
        image_format: The sniffed format.
        max_tokens: Generation ceiling. Small — the answer is four short
            fields, and a large ceiling only buys room for prose nobody reads.

    Returns:
        What the model saw. A photo that is not a fault display comes back
        with that verdict rather than an invented code.

    Raises:
        DomainValidationError: If the model returned no report, or one that
            did not validate.
    """
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    image_block(data, image_format),
                    {
                        "type": "text",
                        "text": "Report what this photograph shows.",
                    },
                ],
            }
        ],
        tools=[recognition_tool_definition()],
        # Forced, not offered. Left to choose, a model asked about an
        # unreadable photo will often answer in prose instead, and prose is
        # what this whole module exists to avoid parsing.
        tool_choice={"type": "tool", "name": RECOGNITION_TOOL_NAME},
    )

    payload = extract_named_tool_payload(message, RECOGNITION_TOOL_NAME)
    if payload is None:
        raise DomainValidationError("the model returned no report of the image")
    return parse_recognition(payload)


def confirmed_context(
    result: FaultRecognitionResult,
    *,
    threshold: float = MIN_FIELD_CONFIDENCE,
) -> tuple[dict[str, str], list[str]]:
    """Split a result into what may be used and what must be confirmed.

    Args:
        result: What the model reported.
        threshold: Confidence floor per field.

    Returns:
        ``(trusted, needs_confirmation)`` — a mapping of field name to value
        for fields that cleared the floor, and the names of fields that were
        read but did not. A field the model did not read at all appears in
        neither: there is nothing to confirm, and asking about it would invite
        the engineer to supply what the photo does not show.

        When the photo is not a readable fault display, everything is empty.
        The verdict is checked first precisely so no field is read off an
        image the model has already said is a wiring diagram.
    """
    if result.verdict is not DisplayVerdict.FAULT_DISPLAY:
        return {}, []

    trusted: dict[str, str] = {}
    unsure: list[str] = []
    fields: tuple[tuple[str, RecognisedField], ...] = (
        ("fault_code", result.fault_code),
        ("brand", result.brand),
        ("model", result.model),
    )
    for name, field in fields:
        if field.value is None:
            continue
        if field.trusted_at(threshold):
            trusted[name] = field.value
        else:
            unsure.append(name)
    return trusted, unsure


def rejection_message(result: FaultRecognitionResult) -> str | None:
    """Explain a photo that could not be used.

    Args:
        result: What the model reported.

    Returns:
        Text for the engineer, or ``None`` if the photo was usable. The two
        rejections say different things because the fix differs: photograph
        something else, versus photograph the same thing again.
    """
    if result.verdict is DisplayVerdict.FAULT_DISPLAY:
        return None

    detail = f" {result.note}" if result.note else ""
    if result.verdict is DisplayVerdict.UNREADABLE:
        return (
            "That looks like a display, but it is not legible enough to read a "
            "code from — glare, blur or angle." + detail + " A straight-on shot "
            "with the screen filling more of the frame usually does it."
        )
    return (
        "That does not look like a fault or alarm display." + detail + " If the "
        "code is on a different screen or a printed label, a photo of that will "
        "work — or type the code in directly."
    )
