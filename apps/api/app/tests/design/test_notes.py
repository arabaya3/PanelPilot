"""Tests for `app/design/notes.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest

from app.design import notes


def test_note_renders_its_english_text_and_keeps_its_params_as_text() -> None:
    made = notes.note("spare_ways", count=2, percent="20")
    assert made.code == "spare_ways"
    assert made.params == {"count": "2", "percent": "20"}
    assert made.text == "Leave 2 spare outgoing ways (20 %)."


@pytest.mark.parametrize(
    "params",
    [{}, {"count": 2}, {"count": 2, "percent": 20, "extra": 1}],
)
def test_a_note_takes_exactly_its_templates_fields(params: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="spare_ways"):
        notes.note("spare_ways", **params)


def test_an_unknown_code_is_a_bug() -> None:
    with pytest.raises(KeyError):
        notes.note("no_such_code")


def test_fields() -> None:
    assert notes.fields("fed_from") == {"board"}
    assert notes.fields("no_fault_level") == set()


def test_every_template_formats_with_its_own_fields() -> None:
    for code in notes.TEMPLATES:
        made = notes.note(code, **dict.fromkeys(notes.fields(code), "x"))
        assert "{" not in made.text, code


def test_the_page_has_a_sentence_for_every_code_in_every_language() -> None:
    """A code the page cannot render falls back to English: catch it here instead."""
    import json
    from pathlib import Path

    messages = Path(__file__).resolve().parents[4] / "web" / "src" / "messages"
    for language in ("en", "ar", "he"):
        catalogue = json.loads((messages / f"{language}.json").read_text(encoding="utf-8"))
        assert set(catalogue["design"]["note"]) == set(notes.TEMPLATES), language


def test_the_page_can_say_every_refusal_the_design_raises() -> None:
    """Every ``code=`` a design refusal carries has a sentence on the page."""
    import json
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[2]
    sources = [
        *(app_dir / "design").glob("*.py"),
        *(app_dir / "ai" / "tools").glob("*.py"),
        app_dir / "domain" / "design.py",
        app_dir / "core" / "errors.py",
    ]
    raised = {
        code
        for source in sources
        for code in re.findall(r'code="([a-z_]+)"', source.read_text(encoding="utf-8"))
    }
    messages = app_dir.parent.parent / "web" / "src" / "messages"
    for language in ("en", "ar", "he"):
        catalogue = json.loads((messages / f"{language}.json").read_text(encoding="utf-8"))
        assert raised <= set(catalogue["design"]["errors"]), language
