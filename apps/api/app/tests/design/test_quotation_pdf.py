"""Tests for `app/design/quotation_pdf.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import pdfplumber

from app.design import quotation, quotation_pdf
from app.models.schemas.design import DesignProject, PriceListEntry, PricingSettings


def _text(data: bytes) -> str:
    with pdfplumber.open(BytesIO(data)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def test_a_complete_quotation(hall_project: DesignProject) -> None:
    entries = [PriceListEntry(key="EKM6-63X-3C63", unit_price=Decimal(55))]
    priced = quotation.price_project(hall_project, PricingSettings(price_list=entries))
    text = _text(quotation_pdf.render_quotation_pdf(priced, hall_project, company="Acme Panels"))
    assert "Acme Panels" in text
    assert "Quotation: Pocket" in text
    assert "55.00" in text
    assert "Total (JOD)" in text


def test_an_incomplete_quotation_says_so(hall_project: DesignProject) -> None:
    priced = quotation.price_project(hall_project, PricingSettings())
    text = _text(quotation_pdf.render_quotation_pdf(priced, hall_project))
    assert "Incomplete" in text
    assert "not priced" in text


def test_typed_text_is_not_read_as_markup_and_arabic_is_drawn(
    hall_project: DesignProject,
) -> None:
    info = hall_project.info.model_copy(update={"name": "<b>Tower & Co</b> برج"})
    project = hall_project.model_copy(update={"info": info})
    priced = quotation.price_project(project, PricingSettings())
    data = quotation_pdf.render_quotation_pdf(priced, project, company="شركة اللوحات")
    with pdfplumber.open(BytesIO(data)) as pdf:
        fonts = {c["fontname"].split("+")[-1] for p in pdf.pages for c in p.chars}
    assert "DejaVuSans-Bold" in fonts or "DejaVuSans" in fonts
    assert "<b>Tower & Co</b>" in _text(data)
