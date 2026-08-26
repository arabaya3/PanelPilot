"""What a vision model may report about a photo of an equipment display.

**Three fields, not a description.** Narrow structured extraction is both more
reliable than "describe this image" and gateable: a confidence attached to
each field lets the fault code be trusted while the brand is not, which is the
common case when a nameplate is out of frame.

**"Not a fault display" is a first-class answer.** A stressed engineer
photographs the wrong thing — a nameplate, a wiring diagram, their own boot.
A model asked only to extract a fault code will find something code-shaped in
almost any image, and a fabricated code sends someone to the wrong procedure
with full confidence. So the model reports whether it is looking at a fault
display at all, and that verdict is checked before any field is read.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

# A recognised value is a short token off a screen, not prose.
ReadValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class DisplayVerdict(StrEnum):
    """Whether the photo shows what the engineer thinks it shows."""

    # A fault, alarm or status display with a readable code.
    FAULT_DISPLAY = "fault_display"
    # Equipment, but not a fault display — a nameplate, a terminal strip, a
    # wiring diagram. Recognisable, just not what was asked for.
    NOT_A_FAULT_DISPLAY = "not_a_fault_display"
    # A display that is there but cannot be read: glare, motion blur, angle,
    # a screen that is off. Distinct from the above because the engineer's
    # fix is different — retake the photo, rather than photograph something
    # else.
    UNREADABLE = "unreadable"


class RecognisedField(BaseModel):
    """One extracted value and how sure the model is of it.

    Attributes:
        value: What was read, verbatim off the screen. ``None`` when the field
            is not visible — which is ordinary, not an error: a fault display
            usually shows a code and rarely shows the manufacturer.
        confidence: How sure the model is, in [0, 1]. Meaningless without
            ``value``, and validated as absent in that case.
    """

    value: ReadValue | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _confidence_needs_a_value(self) -> RecognisedField:
        """Keep a confidence from standing on its own.

        Returns:
            The validated field.

        Raises:
            ValueError: If confidence is claimed for a field that was not
                read. A high confidence next to an absent value is the shape
                of an answer, and something downstream will eventually read
                the number without checking the value beside it.
        """
        if self.value is None and self.confidence != 0.0:
            raise ValueError("confidence without a value means nothing")
        return self

    def trusted_at(self, threshold: float) -> bool:
        """Report whether this field may be used without confirmation.

        Args:
            threshold: The confidence floor.

        Returns:
            ``True`` if a value was read and clears the floor. A field with no
            value is never trusted, whatever the number beside it.
        """
        return self.value is not None and self.confidence >= threshold


class FaultRecognitionResult(BaseModel):
    """What the model saw.

    Attributes:
        verdict: Whether this is a readable fault display at all. Checked
            before any field is read.
        fault_code: The code shown, e.g. "F0001".
        brand: The manufacturer, if visibly identifiable — usually only when a
            logo is in frame.
        model: The equipment model, if visibly identifiable.
        note: What the model saw when the verdict is not ``FAULT_DISPLAY``,
            so the engineer is told what to do instead of just "no".
    """

    verdict: DisplayVerdict
    fault_code: RecognisedField = Field(default_factory=RecognisedField)
    brand: RecognisedField = Field(default_factory=RecognisedField)
    model: RecognisedField = Field(default_factory=RecognisedField)
    note: str | None = None

    @model_validator(mode="after")
    def _a_rejected_photo_reads_nothing(self) -> FaultRecognitionResult:
        """Keep a rejection from carrying extracted values.

        Returns:
            The validated result.

        Raises:
            ValueError: If a non-fault-display verdict carries a fault code.
                That combination is the exact failure this schema exists to
                prevent: a model that has decided the photo is a wiring
                diagram and reported a code anyway has invented the code, and
                a caller reading fields before the verdict would use it.
        """
        if self.verdict is not DisplayVerdict.FAULT_DISPLAY and self.fault_code.value is not None:
            raise ValueError(
                f"verdict is {self.verdict.value} but a fault code was reported; "
                "a code read off something that is not a fault display is invented"
            )
        return self
