"""Server-sent events for a streamed diagnostic turn.

**The final event is the whole answer.** Progress events say what stage the
turn reached; they never carry a fragment of the answer itself. A client that
ignores everything except ``result`` loses nothing.

That is deliberate. Streaming a partially-built answer would put text in front
of an engineer before the guardrail had ruled on whether it may be shown at
all, and a refusal that arrives after three paragraphs of a confident-sounding
draft is not a refusal. The stages exist because retrieval and generation take
seconds, and an engineer watching a blank panel cannot tell a slow answer from
a hung one.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

# The stages a turn passes through. ``result`` is terminal and always sent.
EventName = Literal["retrieving", "generated", "refused", "result"]


class DiagnosisEvent(BaseModel):
    """One server-sent event.

    Attributes:
        event: Which stage this reports.
        data: The payload. Empty for progress stages; the complete
            ``DiagnosticResponse`` for ``result``.
    """

    event: EventName
    data: dict[str, Any] = Field(default_factory=dict)

    def render(self) -> str:
        """Render as an SSE frame.

        Returns:
            The wire format: an ``event:`` line, a ``data:`` line, and the
            blank line that terminates the frame. The payload is serialised as
            one line because a raw newline inside ``data:`` would split it
            into two frames and truncate the answer.
        """
        payload = json.dumps(self.data, separators=(",", ":"))
        return f"event: {self.event}\ndata: {payload}\n\n"
