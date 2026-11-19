"""Request and response bodies of server-backed design drafts (slice 1).

A draft body is checked for its structure only (the shape the design page
keeps a draft in): it is unsaved work, never engineering-validated, and a
save of it goes through the normal save with every existing check.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.models.schemas.design import StrictModel

#: The largest draft body accepted, serialized as compact JSON (bytes).
DRAFT_BODY_MAX_BYTES = 512 * 1024


class DraftInfo(StrictModel):
    """The project details as typed."""

    name: str = Field(max_length=200)
    number: str = Field(max_length=200)
    customer: str = Field(max_length=200)
    consultant: str = Field(max_length=200)
    contractor: str = Field(max_length=200)


class DraftConfiguration(StrictModel):
    """Unsaved configuration edits, and whether "save without it" was chosen."""

    doc: dict[str, Any]
    dropUnrecoverable: bool  # noqa: N815 - the page's own field name
    confirmed: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class DraftBody(StrictModel):
    """What the design page keeps of unsaved work (never a token or identity)."""

    clean: str = Field(max_length=DRAFT_BODY_MAX_BYTES)
    info: DraftInfo
    boards: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    profileText: str = Field(max_length=100_000)  # noqa: N815
    language: str | None = Field(default=None, max_length=16)
    market: str = Field(max_length=32)
    control: list[Any] | None = Field(default=None, max_length=200)
    ai: dict[str, Any] | None = None
    configuration: DraftConfiguration | None = None


class DraftWrite(StrictModel):
    """Keep a draft.

    Attributes:
        request_id: Chosen by the client for this write; a retry sends the same.
        issued_at: When the client first sent it; refused after 24 hours.
        if_match: The token the client last saw, or ``None`` to say it saw no
            draft (and no deleted one) for this project.
        base_revision: The saved revision the work was made from.
        body: The work.
    """

    request_id: uuid.UUID
    issued_at: datetime
    if_match: str | None = Field(default=None, max_length=80)
    base_revision: int = Field(ge=1)
    body: DraftBody


class DraftDelete(StrictModel):
    """Delete a draft (it becomes a tombstone with a new token)."""

    request_id: uuid.UUID
    issued_at: datetime
    if_match: str = Field(max_length=80)


class DraftState(StrictModel):
    """A draft as the server holds it, for its author only.

    Attributes:
        project_id: The saved project.
        token: The concurrency token: send it as ``if_match`` with the next write.
        state: ``live``, or ``deleted`` (a tombstone, with no body).
        base_revision: The saved revision the work was made from.
        updated_at: When it was last written.
        body: The work; ``None`` when deleted or when not asked for.
        replayed: ``True`` when this answers a retry of a write already applied.
    """

    project_id: uuid.UUID
    token: str
    state: Literal["live", "deleted"]
    base_revision: int
    updated_at: datetime
    body: DraftBody | None = None
    replayed: bool = False


class DraftLookup(StrictModel):
    """The caller's draft of one project, if any, and whether writes are on."""

    writes_enabled: bool
    draft: DraftState | None = None


class DraftList(StrictModel):
    """The caller's live drafts (no bodies), and whether writes are on."""

    writes_enabled: bool
    drafts: list[DraftState]
