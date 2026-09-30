"""Terminal block selection.

Pure functions. Each selection cites the catalogue page it came from.

The range is Siemens 8WH1 screw through-type terminals, as catalogue LV 10
(10/2022) prints them: for each terminal size, the largest load current it
carries and the rigid conductor cross-sections it clamps. A terminal is
chosen for a conductor, so both have to fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.errors import ValidationError
from app.models.schemas.search import Citation

CATALOGUE_ID = "siemens-lv10-terminal-blocks-2022-10"
CATALOGUE_TITLE = "Catalog LV 10, Terminal Blocks (10/2022)"


@dataclass(frozen=True)
class _Terminal:
    """One column of the 8WH1 through-type terminal tables."""

    size_mm2: str
    max_current_a: str
    #: Connection capacity, one rigid conductor, mm².
    rigid_min_mm2: str
    rigid_max_mm2: str
    #: Through-type terminal, gray.
    article: str
    #: PE through-type terminal of the same size, where one is listed.
    pe_article: str | None
    #: PDF page of the column.
    page: int


#: 8WH1 through-type terminals (LV 10 pp. 14/44-14/46, PDF pp. 48-50). The
#: 35 mm² PE terminal is printed as its own column without a load current;
#: 150 and 240 mm² list no PE terminal.
_TERMINALS: tuple[_Terminal, ...] = (
    _Terminal("2.5", "32", "0.14", "4", "8WH1000-0AF00", "8WH1000-0CF07", 48),
    _Terminal("4", "41", "0.14", "6", "8WH1000-0AG00", "8WH1000-0CG07", 48),
    _Terminal("6", "57", "0.2", "10", "8WH1000-0AH00", "8WH1000-0CH07", 49),
    _Terminal("10", "76", "0.5", "16", "8WH1000-0AJ00", "8WH1000-0CJ07", 49),
    _Terminal("16", "101", "1.5", "25", "8WH1000-0AK00", "8WH1000-0CK07", 49),
    _Terminal("35", "150", "1.5", "50", "8WH1000-0AM00", "8WH1000-0CM07", 49),
    _Terminal("50", "150", "16", "70", "8WH1000-0AN00", "8WH1000-0CN07", 50),
    _Terminal("70", "192", "16", "95", "8WH1000-0AP00", "8WH1000-0CP07", 50),
    _Terminal("95", "232", "25", "95", "8WH1000-0AQ00", "8WH1000-0CQ07", 50),
    _Terminal("150", "309", "35", "150", "8WH1000-0AS00", None, 50),
    _Terminal("240", "415", "70", "240", "8WH1000-0AU00", None, 50),
)


@dataclass(frozen=True)
class TerminalSelection:
    """The terminal chosen for a conductor.

    Attributes:
        size_mm2: The terminal size.
        max_current_a: Its largest load current, A.
        article: The through-type terminal's article number.
        pe_article: The PE terminal of the same size, if the catalogue lists
            one.
        source: The catalogue page.
    """

    size_mm2: str
    max_current_a: str
    article: str
    pe_article: str | None
    source: Citation


def _citation(page: int) -> Citation:
    """Cite a page of the catalogue."""
    return Citation(
        document_id=CATALOGUE_ID,
        document_title=CATALOGUE_TITLE,
        manufacturer="Siemens",
        page=page,
        section="8WH1 through-type terminals",
    )


def select_terminal(*, cross_section_mm2: Decimal, current_a: Decimal) -> TerminalSelection:
    """Select the smallest 8WH1 terminal that clamps a conductor and carries its current.

    Source:
        Siemens, Catalog LV 10 (10/2022), Terminal Blocks, "8WH screw
        terminals: 8WH1 through-type terminals", pp. 14/44-14/46.

    Args:
        cross_section_mm2: The copper conductor's cross-section.
        current_a: The circuit's design current.

    Returns:
        The terminal, with its PE counterpart where listed.

    Raises:
        ValidationError: If an argument is not a positive number, or no
            terminal both clamps the conductor and carries the current.
    """
    for name, value in (("cross_section_mm2", cross_section_mm2), ("current_a", current_a)):
        if not value.is_finite() or value <= 0:
            raise ValidationError(f"{name} must be positive, got {value}")
    for terminal in _TERMINALS:
        fits = (
            Decimal(terminal.rigid_min_mm2) <= cross_section_mm2 <= Decimal(terminal.rigid_max_mm2)
        )
        if fits and Decimal(terminal.max_current_a) >= current_a:
            return TerminalSelection(
                size_mm2=terminal.size_mm2,
                max_current_a=terminal.max_current_a,
                article=terminal.article,
                pe_article=terminal.pe_article,
                source=_citation(terminal.page),
            )
    raise ValidationError(
        f"no 8WH1 through-type terminal clamps {cross_section_mm2} mm² and carries "
        f"{current_a} A (the range ends at 240 mm², 415 A)"
    )
