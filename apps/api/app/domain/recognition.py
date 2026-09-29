"""Upload a display photo and read the fault code off it in one step.

The composition of BE-009 and AI-008. Storage rules stay in
``app.domain.images`` and the vision call stays in ``app.ai.recognition``;
this module only decides how the two meet, which is the one decision neither
of them should own.

**Recognition is best-effort; the upload is not.** Once the bytes have been
accepted and stored, the engineer has an ``image_id`` they can reference, and
a model that is down or answers nonsense must not turn that into an error. So
a failed recognition returns the stored image with ``recognition`` absent —
the client already renders that as "uploaded, please describe the fault" —
rather than failing a request whose side effect has already happened.

**Validation failures are not swallowed.** An empty, oversized or non-image
upload raises exactly as it did before: those are the caller's problem, and
nothing is sent to the model for a file that was never stored.

**A reading costs one free question**, charged the way a diagnosis is: only
for a reading the engineer actually receives. A spent allowance skips the
model call entirely, and a failed call charges nothing. The charge is the same
row-locked ``consume_free_question`` a diagnosis uses, so concurrent uploads
cannot exceed the limit between them.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.ai.recognition import recognise_fault_display
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.observability import timed
from app.domain import images as images_domain
from app.domain.auth import consume_free_question, get_quota
from app.domain.storage import ObjectStore
from app.models.schemas.images import ImageFormat, ImageUploadResponse
from app.models.schemas.recognition import FaultRecognitionResult

logger = structlog.get_logger(__name__)


def upload_and_recognise(
    *,
    session: Session,
    store: ObjectStore,
    tenant_id: str,
    data: bytes,
) -> ImageUploadResponse:
    """Store a photo, then read what it shows.

    Args:
        session: Open database session, for the free-question charge. This
            function commits it when a reading is charged.
        store: Where the bytes go.
        tenant_id: The uploading tenant.
        data: The uploaded bytes.

    Returns:
        The stored image's id, with the recogniser's report attached when one
        was produced. The report is the model's raw verdict and per-field
        confidences; whether a field may be used without confirmation is
        decided by the client against ``MIN_FIELD_CONFIDENCE``, the same
        threshold ``confirmed_context`` applies.

    Raises:
        ValidationError: If the upload is empty, too large, or not an image.
            Raised before any model call, by ``images_domain.store_image``.
    """
    stored = images_domain.store_image(store=store, tenant_id=tenant_id, data=data)
    # Sniffed again rather than threaded out of `store_image`, whose return
    # type is the public response. Cheap — sixteen bytes — and it cannot
    # disagree with what was stored, because it reads the same bytes.
    image_format = images_domain.sniff_format(data)

    # An unlocked read, used only to avoid paying for a model call whose
    # reading could not be delivered. The locked charge below is the gate.
    if get_quota(session=session, tenant_id=tenant_id).questions_remaining == 0:
        logger.info("recognition.skipped_quota", tenant_id=tenant_id, image_id=stored.image_id)
        return ImageUploadResponse(image_id=stored.image_id, recognition=None)

    recognition = _recognise_or_none(
        data=data,
        image_format=image_format,
        tenant_id=tenant_id,
        image_id=stored.image_id,
    )
    if recognition is not None:
        recognition = _charge_or_withhold(
            session=session,
            recognition=recognition,
            tenant_id=tenant_id,
            image_id=stored.image_id,
        )
    return ImageUploadResponse(image_id=stored.image_id, recognition=recognition)


def _charge_or_withhold(
    *,
    session: Session,
    recognition: FaultRecognitionResult,
    tenant_id: str,
    image_id: str,
) -> FaultRecognitionResult | None:
    """Charge a free question for a reading, or withhold the reading.

    Args:
        session: Open database session; committed when the charge succeeds.
        recognition: The reading to deliver.
        tenant_id: Whose allowance pays for it.
        image_id: For the log line only.

    Returns:
        The reading once paid for, or ``None`` when the allowance ran out
        between the pre-check and now — another request spent the last
        question. Withheld rather than given away, so the limit holds exactly.
    """
    try:
        consume_free_question(session=session, tenant_id=tenant_id)
    except ValidationError:
        session.rollback()
        logger.info("recognition.withheld_quota", tenant_id=tenant_id, image_id=image_id)
        return None
    session.commit()
    return recognition


def _recognise_or_none(
    *,
    data: bytes,
    image_format: ImageFormat,
    tenant_id: str,
    image_id: str,
) -> FaultRecognitionResult | None:
    """Run the recogniser, degrading to no report if it fails.

    Args:
        data: The stored image.
        image_format: Its sniffed format.
        tenant_id: For the log line only.
        image_id: For the log line only.

    Returns:
        The report, or ``None`` if the model could not be reached or returned
        something that did not validate.
    """
    try:
        with timed("recognition"):
            return recognise_fault_display(
                _anthropic_client(),
                model=get_settings().llm_model,
                data=data,
                image_format=image_format,
            )
    except Exception:
        # Broad for the same reason the diagnosis stream is: the call reaches
        # Anthropic, which can fail in ways this layer cannot enumerate, and
        # every one of them must still leave the engineer with the image they
        # uploaded. Logged with the traceback so the degradation is visible to
        # operators even though it is not visible as an error to the user.
        # Ids only — never the image bytes.
        logger.exception("recognition.failed", tenant_id=tenant_id, image_id=image_id)
        return None


def _anthropic_client() -> Any:
    """Return a Claude client.

    Constructed per call rather than at import time so tests can substitute
    one without a live key, matching ``app.domain.diagnostics``.

    Returns:
        An Anthropic client.
    """
    import anthropic

    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
