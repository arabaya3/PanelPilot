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
"""

from __future__ import annotations

from typing import Any

import structlog

from app.ai.anthropic_client import get_llm_client
from app.ai.recognition import recognise_fault_display
from app.core.config import get_settings
from app.core.observability import timed
from app.domain import images as images_domain
from app.domain.storage import ObjectStore
from app.models.schemas.images import ImageFormat, ImageUploadResponse
from app.models.schemas.recognition import FaultRecognitionResult

logger = structlog.get_logger(__name__)


def upload_and_recognise(
    *,
    store: ObjectStore,
    tenant_id: str,
    data: bytes,
) -> ImageUploadResponse:
    """Store a photo, then read what it shows.

    Args:
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

    recognition = _recognise_or_none(
        data=data,
        image_format=image_format,
        tenant_id=tenant_id,
        image_id=stored.image_id,
    )
    return ImageUploadResponse(image_id=stored.image_id, recognition=recognition)


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
                model=get_settings().generation_model,
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
    """Return the shared Claude client.

    An accessor so tests can substitute one without a live key, matching
    ``app.domain.diagnostics``. Shared rather than built per call: see
    ``app.ai.anthropic_client`` for the timeouts it carries.

    Returns:
        An Anthropic client.
    """
    return get_llm_client()
