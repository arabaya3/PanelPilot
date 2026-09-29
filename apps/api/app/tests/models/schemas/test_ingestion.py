"""Tests for `app/models/schemas/ingestion.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pydantic
import pytest

from app.models.schemas.ingestion import MAX_DOCUMENT_URLS, MAX_SEED_URLS, CrawlJobRequest


def _urls(count: int) -> list[str]:
    return [f"https://library.abb.com/{i}.pdf" for i in range(count)]


def test_a_request_at_the_caps_is_accepted() -> None:
    request = CrawlJobRequest(
        source_id="abb", seed_urls=_urls(MAX_SEED_URLS), document_urls=_urls(MAX_DOCUMENT_URLS)
    )

    assert len(request.seed_urls) == MAX_SEED_URLS
    assert len(request.document_urls) == MAX_DOCUMENT_URLS


@pytest.mark.parametrize(
    "overrides",
    [
        {"seed_urls": _urls(MAX_SEED_URLS + 1)},
        {"document_urls": _urls(MAX_DOCUMENT_URLS + 1)},
    ],
)
def test_a_request_past_either_cap_is_refused(overrides: dict[str, list[str]]) -> None:
    # Each URL is a request the crawler makes; an uncapped list is an uncapped
    # crawl one POST away.
    with pytest.raises(pydantic.ValidationError):
        CrawlJobRequest(source_id="abb", **overrides)


def test_the_lists_default_empty_and_unshared() -> None:
    first = CrawlJobRequest(source_id="abb")
    first.seed_urls.append("https://library.abb.com/a")

    assert CrawlJobRequest(source_id="abb").seed_urls == []
