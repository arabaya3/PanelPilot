"""The one Claude client every model call goes through.

Built once per process and shared. It used to be constructed per request,
which paid a fresh connection pool and TLS handshake on every question and
left each one to be closed by the garbage collector.

It also inherited the SDK's defaults: a 600-second read timeout and two
retries. A request thread — and, before the diagnosis path stopped holding
one, a database connection — could be pinned for half an hour by a stalled
response, and a slow provider exhausted the thread pool, taking the health
checks down with it. The bounds below are what a person waiting for an
answer will tolerate, not what the transport allows.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config import get_settings

#: Seconds to wait for a response. A structured diagnosis is a few hundred
#: output tokens; a call that has produced nothing in this long is stalled,
#: and failing it lets the caller show a terminal refusal instead of a spinner.
LLM_READ_TIMEOUT_S = 90.0

#: Seconds to establish a connection. Short: a provider that cannot accept a
#: connection in this long will not answer in time either.
LLM_CONNECT_TIMEOUT_S = 5.0

#: Retries on connection errors, 429 and 5xx, with the SDK's own backoff. One,
#: not the default two: every retry is another full wait inside a request the
#: engineer is watching.
LLM_MAX_RETRIES = 1


@lru_cache(maxsize=1)
def get_anthropic_client() -> Any:
    """Return the process-wide Claude client.

    Imported lazily so a process that never calls a model does not need the
    key at import time, and cached so every caller shares one connection pool.
    Tests substitute the callers' own accessors rather than this one.

    Returns:
        An Anthropic client with bounded timeouts and retries.
    """
    import anthropic

    return anthropic.Anthropic(
        api_key=get_settings().anthropic_api_key.get_secret_value(),
        # The SDK's own Timeout type: it vendors its HTTP client, and rejects
        # one built from the httpx package.
        timeout=anthropic.Timeout(LLM_READ_TIMEOUT_S, connect=LLM_CONNECT_TIMEOUT_S),
        max_retries=LLM_MAX_RETRIES,
    )
