"""The three PDF exports are vector and text only.

reportlab imports Pillow to embed raster images, and pdfplumber uses it to
render page images. None of our exports draws a raster image, so neither
library's image code runs on them; that keeps an image-decoder flaw off the
export path. These tests pin it through the full export entry point, with the
same pdfplumber parse a reader of the file would do. A logo or a photo added to
an export later fails here, which is the moment to decide on the image path
deliberately.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import cast

import pdfplumber
import pytest
from sqlalchemy.orm import Session

from app.domain import billing
from app.domain import design as design_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.design import (
    DesignExportRequest,
    DesignProject,
    ExportFormat,
    PriceListEntry,
    PricingSettings,
)


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


@pytest.mark.parametrize(
    ("export_format", "expected_text"),
    [
        (ExportFormat.PDF, "DBG-HALL"),
        (ExportFormat.CALCULATIONS_PDF, "Calculations report: Pocket"),
        (ExportFormat.QUOTATION_PDF, "Quotation: Pocket"),
    ],
)
def test_a_pdf_export_parses_and_embeds_no_raster_image(
    hall_project: DesignProject,
    export_format: ExportFormat,
    expected_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The plan check is billing's concern and has its own tests.
    monkeypatch.setattr(billing, "require_feature", lambda **_: None)
    pricing = PricingSettings(
        price_list=[PriceListEntry(key="EKM6-63X-3C63", unit_price=Decimal(55))]
    )
    request = DesignExportRequest(project=hall_project, format=export_format, pricing=pricing)

    exported = design_domain.export_design(
        session=cast(Session, object()), user=_user(), request=request
    )

    assert exported.content.startswith(b"%PDF")
    with pdfplumber.open(BytesIO(exported.content)) as pdf:
        assert pdf.pages
        assert expected_text in "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert [image for page in pdf.pages for image in page.images] == []
