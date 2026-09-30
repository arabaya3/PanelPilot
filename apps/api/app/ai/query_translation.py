"""An English search query for a question asked in another language.

The corpus is manufacturers' English manuals. The vector leg of retrieval
crosses languages, but the keyword leg matches nothing in an Arabic or Hebrew
question, and a parameter table is found by its words. Found live: an Arabic
question about the emergency-stop source never reached the passage that
answers it, and the model filled the gap with general advice.

Retrieval only. The answer is still composed in the engineer's language; this
translates the search, not the conversation. It answers nothing and cites
nothing, so the cite-or-refuse gate has no say over it -- the architecture
test names it beside the photo recogniser for the same reason.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.ai.structured_output import extract_named_tool_payload

logger = structlog.get_logger(__name__)

QUERY_TOOL_NAME = "emit_search_query"

#: A search query is a line, not an essay.
MAX_OUTPUT_TOKENS = 200

SYSTEM_PROMPT = """You turn an engineer's question into an English search query
for industrial equipment manuals. Keep every fault code, parameter number,
model name and unit exactly as written. Output only the query, through the tool.
"""


def english_search_query(question: str, *, client: Any, model: str) -> str:
    """Translate a question into an English search query.

    Args:
        question: The engineer's question, in any language.
        client: The model client.
        model: The model id.

    Returns:
        The query, or the question unchanged if no usable query came back --
        a worse search, never a failed turn.
    """
    try:
        message = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
            tools=[
                {
                    "name": QUERY_TOOL_NAME,
                    "description": "Return the English search query.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            tool_choice={"type": "tool", "name": QUERY_TOOL_NAME},
        )
    except Exception:
        logger.warning("query_translation.failed", exc_info=True)
        return question
    payload = extract_named_tool_payload(message, QUERY_TOOL_NAME) or {}
    query = payload.get("query")
    return query.strip() if isinstance(query, str) and query.strip() else question
