"""Equipment photo upload.

Thin by contract: read the bytes, call one domain function, return. Every
rule about what may be stored lives in ``app.domain.images`` so a second
caller cannot get a different answer.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUserDep, ObjectStoreDep, SessionDep
from app.domain import images as images_domain
from app.domain import recognition as recognition_domain
from app.models.schemas.images import ImageUploadResponse

router = APIRouter()


@router.post("", response_model=ImageUploadResponse)
async def upload_image(
    user: CurrentUserDep,
    session: SessionDep,
    store: ObjectStoreDep,
    file: Annotated[UploadFile, File()],
) -> ImageUploadResponse:
    """Accept a photo of an equipment display and read the fault code off it.

    The declared content type is deliberately not passed on: the domain
    sniffs the bytes, and forwarding a client-supplied type would invite a
    future reader to trust it.
    """
    # Read with a ceiling rather than `await file.read()`, which would buffer
    # an arbitrarily large upload before anything could reject it. One byte
    # over the limit is enough to fail, and is not retained.
    data = await file.read(images_domain.MAX_IMAGE_BYTES + 1)
    # The recogniser is a blocking model call of several seconds; run on the
    # threadpool so it does not stall the event loop for every other request.
    return await run_in_threadpool(
        recognition_domain.upload_and_recognise,
        session=session,
        store=store,
        tenant_id=user.tenant_id,
        data=data,
    )
