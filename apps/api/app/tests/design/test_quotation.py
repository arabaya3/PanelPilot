"""Tests for `app/design/quotation.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.design import quotation
from app.models.schemas.design import (
    Cable,
    DesignProject,
    Device,
    DeviceKind,
    PriceListEntry,
    PricingSettings,
)


def _entries(project: DesignProject) -> list[PriceListEntry]:
    """A price for every device and cable key in the project."""
    keys: set[str] = set()
    for board in project.boards:
        keys.update(quotation.device_key(d) for d in board.devices)
        keys.update(quotation.cable_key(c) for c in board.cables)
    entries = [PriceListEntry(key=key, unit_price=Decimal(10)) for key in keys]
    # The incomer has an article: its order number wins over its rating key.
    entries.append(
        PriceListEntry(key="EKM6-63X-3C63", description="ETEK MCB", unit_price=Decimal(55))
    )
    return entries


def test_a_fully_priced_board(hall_project: DesignProject) -> None:
    pricing = PricingSettings(
        currency="JOD",
        price_list=_entries(hall_project),
        cable_length_m=Decimal(20),
        labour_per_circuit=Decimal(5),
        labour_per_board=Decimal(50),
        enclosure_price=Decimal(120),
        markup_percent=Decimal(10),
        vat_percent=Decimal(16),
    )
    result = quotation.price_project(hall_project, pricing)
    assert result.complete
    assert result.unpriced == []
    incomer = next(line for line in result.lines if line.key == "EKM6-63X-3C63")
    # Named by the designed part, not the supplier's "ETEK MCB".
    assert incomer.description == "ETEK EKM6-63X-3C63"
    assert incomer.total == Decimal("55.00")
    board = hall_project.boards[0]
    # Every device and cable is on exactly one line.
    covered = sum(len(line.designations) for line in result.lines)
    assert covered == len(board.devices) + len(board.cables)
    devices = len(board.devices) - 1
    cable_metres = len(board.cables) * 20
    materials = Decimal(55) + Decimal(10) * devices + Decimal(10) * cable_metres
    assert result.materials == materials
    labour = Decimal(5) * len(board.circuits) + Decimal(50) + Decimal(120)
    assert result.labour == labour
    assert result.markup == ((materials + labour) * Decimal("0.1")).quantize(Decimal("0.01"))
    assert result.total == (materials + labour + result.markup + result.vat)


def test_unpriced_lines_are_named_and_not_guessed(hall_project: DesignProject) -> None:
    result = quotation.price_project(hall_project, PricingSettings())
    assert not result.complete
    assert result.materials == 0
    assert all(line.total is None for line in result.lines)
    assert any("(no price)" in item for item in result.unpriced)
    # No default length: cables are unpriced for want of one.
    assert any(item.startswith("cable:") and "(no length)" in item for item in result.unpriced)
    reasons = {line.unit: line.missing for line in result.lines}
    assert reasons == {"pcs": "price", "m": "length"}


def test_keys() -> None:
    breaker = Device(
        id="a", kind=DeviceKind.CIRCUIT_BREAKER, poles=1, rated_current_a=Decimal(16), curve="C"
    )
    rcd = Device(
        id="b",
        kind=DeviceKind.RESIDUAL_CURRENT_DEVICE,
        poles=4,
        rated_current_a=Decimal(40),
        residual_current_ma=Decimal(30),
    )
    assert quotation.device_key(breaker) == "circuit_breaker:1P:C16"
    assert quotation.device_key(rcd) == "residual_current_device:4P:40A:30mA"
    contactor = Device(id="k", kind=DeviceKind.CONTACTOR, poles=2, rated_current_a=Decimal(20))
    assert quotation.device_key(contactor) == "contactor:2P:20A"
    cable = Cable(id="c", cores=3, cross_section_mm2=Decimal("2.5"))
    assert quotation.cable_key(cable) == "cable:3G2.5:Cu:PVC"


def test_keys_match_regardless_of_case_and_spaces(hall_project: DesignProject) -> None:
    entries = [PriceListEntry(key=" CIRCUIT_BREAKER : 1P : C16 ", unit_price=Decimal(4))]
    result = quotation.price_project(hall_project, PricingSettings(price_list=entries))
    assert any(line.unit_price == Decimal(4) for line in result.lines)


def test_read_price_list() -> None:
    data = b"Item,Description,Unit price\ncircuit_breaker:1P:C16,MCB 1P C16,4.50\nEKM6-63X,,1,200\n,,\n"
    entries = quotation.read_price_list(data)
    assert entries[0] == PriceListEntry(
        key="circuit_breaker:1P:C16", description="MCB 1P C16", unit_price=Decimal("4.50")
    )
    assert len(entries) == 2
    arabic = "الكود,السعر\nX1,3\n".encode()
    assert quotation.read_price_list(arabic)[0].unit_price == Decimal(3)


@pytest.mark.parametrize(
    ("data", "message"),
    [(b"Name,Colour\nx,red\n", "no header row"), (b"Key,Price\n,\nx,abc\n", "no row with both")],
)
def test_a_bad_price_list_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        quotation.read_price_list(data)


def test_quotation_csv(hall_project: DesignProject) -> None:
    result = quotation.price_project(hall_project, PricingSettings(currency="USD"))
    text = quotation.quotation_csv(result)
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text.removeprefix("﻿"))))
    assert rows[0][0] == "Description"
    assert any(row[0] == "Total (USD)" for row in rows)
    assert rows[-1][0].startswith("Not priced: ")


def test_an_over_long_price_list_key_is_skipped_not_truncated() -> None:
    """Truncating at 120 made two different long keys one key; the second won."""
    stem = "x" * 120
    data = f"Key,Price\n{stem}A,1\n{stem}B,2\nshort,3\n".encode()
    entries = quotation.read_price_list(data)
    assert [entry.key for entry in entries] == ["short"]


def test_a_key_of_exactly_the_limit_is_kept() -> None:
    key = "k" * 120
    entries = quotation.read_price_list(f"Key,Price\n{key},1\n".encode())
    assert [entry.key for entry in entries] == [key]


def test_a_price_with_spreadsheet_float_noise_is_rounded_to_the_bound() -> None:
    entries = quotation.read_price_list(b"Key,Price\na,0.30000000000000004\n")
    assert entries[0].unit_price == Decimal("0.300000")


@pytest.mark.parametrize(
    "field", ["labour_per_circuit", "labour_per_board", "enclosure_price", "markup_percent"]
)
def test_pricing_amounts_refuse_more_than_six_decimal_places(field: str) -> None:
    with pytest.raises(PydanticValidationError):
        PricingSettings.model_validate({field: "1.0000001"})
    assert getattr(PricingSettings.model_validate({field: "1.000001"}), field) == Decimal(
        "1.000001"
    )


def test_price_list_entries_refuse_more_than_six_decimal_places() -> None:
    with pytest.raises(PydanticValidationError):
        PriceListEntry.model_validate({"key": "a", "unit_price": "1.0000001"})


def test_a_cable_length_feeding_the_totals_is_bounded() -> None:
    """A sent project's cable length multiplied a price with no bound at all."""
    with pytest.raises(PydanticValidationError):
        Cable(id="c", cores=3, cross_section_mm2=Decimal("2.5"), length_m=Decimal("1e30"))
    with pytest.raises(PydanticValidationError):
        Cable.model_validate({"id": "c", "cores": 3, "cross_section_mm2": "2.5", "length_m": "NaN"})


@pytest.mark.parametrize("language", ["ar", "he"])
def test_generated_descriptions_are_in_the_profile_language(
    hall_project: DesignProject, language: str
) -> None:
    """D4: "Circuit-breaker 4P C63" and the labour lines stayed English in ar and he."""
    pricing = PricingSettings(
        cable_length_m=Decimal(10),
        labour_per_circuit=Decimal(5),
        labour_per_board=Decimal(50),
        enclosure_price=Decimal(100),
    )
    english = quotation.price_project(hall_project, pricing)
    said = quotation.price_project(hall_project, pricing, language=language)
    assert len(said.lines) == len(english.lines)
    for mine, theirs in zip(said.lines, english.lines, strict=True):
        # Units stay codes, as keys and totals do: the page says them.
        assert (mine.unit, mine.key, mine.total) == (theirs.unit, theirs.key, theirs.total)
    texts = [line.description for line in said.lines]
    for word in ("Circuit-breaker", "Cable ", "Wiring", "Assembly", "Enclosure"):
        assert not any(word in text for text in texts), (word, texts)
    # The rating stays as written: "4P C63" is not a word.
    breaker = next(line for line in english.lines if line.key.startswith("circuit_breaker:"))
    rating = breaker.description.removeprefix("Circuit-breaker ")
    assert any(text.endswith(rating) for text in texts)


def test_supplier_and_catalogue_text_is_never_translated(hall_project: DesignProject) -> None:
    entries = [*_entries(hall_project)]
    said = quotation.price_project(hall_project, PricingSettings(price_list=entries), language="ar")
    incomer = next(line for line in said.lines if line.key == "EKM6-63X-3C63")
    # Named by the designed part, not the supplier's "ETEK MCB".
    assert incomer.description == "ETEK EKM6-63X-3C63"


def test_a_supplier_description_never_names_the_line(hall_project: DesignProject) -> None:
    """The line's identity is the designed item's; a price list cannot rename a device."""
    plain = quotation.price_project(
        hall_project, PricingSettings(price_list=_entries(hall_project))
    )
    forged = [
        entry.model_copy(update={"description": "FORGED 400 A MCCB"})
        for entry in _entries(hall_project)
    ]
    priced = quotation.price_project(hall_project, PricingSettings(price_list=forged))
    assert [line.description for line in priced.lines] == [line.description for line in plain.lines]
    assert not any("FORGED" in line.description for line in priced.lines)


@pytest.mark.parametrize(
    "keys",
    [("A30", "A30"), ("A30", " a30 "), ("ABB A30", "ABBA30"), ("cable:3G2.5", "CABLE:3G 2.5")],
)
def test_price_list_keys_that_collide_are_refused_by_name(keys: tuple[str, str]) -> None:
    entries = [{"key": key, "unit_price": str(n + 1)} for n, key in enumerate(keys)]
    with pytest.raises(PydanticValidationError, match="duplicate price-list key"):
        PricingSettings.model_validate({"price_list": entries})


def test_an_imported_price_list_with_colliding_keys_is_refused() -> None:
    data = b"Key,Price,Description\nA30,40,first\nA 30,55,second\n"
    with pytest.raises(ValidationError, match="A 30"):
        quotation.read_price_list(data)


def test_a_duplicate_key_import_error_carries_its_code() -> None:
    with pytest.raises(ValidationError) as caught:
        quotation.read_price_list(b"Key,Price\nA30,40\nA 30,55\n")
    assert caught.value.code == "price_list_duplicate_key"
    assert caught.value.params == {"keys": "A 30"}
