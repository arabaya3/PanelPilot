"""Tests for robots.txt fetching and evaluation.

The crawler tests exercise this through a whole crawl; these cover the decision
itself, where the distinctions are finer than a crawl run shows. In particular
the three ways a robots.txt can be *absent* — 404, unreachable host, and a
403 — mean three different things, and only one of them is permission.
"""

from __future__ import annotations

import httpx
import pytest

from app.ingestion.robots import (
    USER_AGENT,
    RobotsDisallowedError,
    RobotsUnavailableError,
    fetch_policy,
    require_allowed,
    robots_url_for,
)

SEED = "https://library.abb.com/manuals/acs880"


def client_returning(status: int, body: str = "") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body.encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def failing_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- locating the file -------------------------------------------------------


def test_robots_lives_at_the_host_root_whatever_the_url() -> None:
    # A per-directory robots.txt is not a thing; asking the wrong place would
    # 404 and be read as permission.
    assert robots_url_for(SEED) == "https://library.abb.com/robots.txt"
    assert robots_url_for("https://library.abb.com/") == "https://library.abb.com/robots.txt"


def test_the_scheme_and_host_are_preserved() -> None:
    assert robots_url_for("http://example.test:8080/a/b") == "http://example.test:8080/robots.txt"


# --- what each response means ------------------------------------------------


def test_a_404_permits_everything() -> None:
    # The standard says an absent file imposes no restrictions, and treating
    # it as a failure would lock us out of compliant sources.
    policy = fetch_policy(source_id="abb", seed_url=SEED, client=client_returning(404))
    assert policy.allows(SEED) is True


def test_a_403_is_unreadable_not_permissive() -> None:
    # "We could not check" must never resolve to "so we proceeded" — and a 403
    # on robots.txt specifically is a strong hint we are unwelcome.
    with pytest.raises(RobotsUnavailableError):
        fetch_policy(source_id="abb", seed_url=SEED, client=client_returning(403))


def test_a_500_is_unreadable() -> None:
    with pytest.raises(RobotsUnavailableError):
        fetch_policy(source_id="abb", seed_url=SEED, client=client_returning(500))


def test_an_unreachable_host_is_unreadable() -> None:
    with pytest.raises(RobotsUnavailableError):
        fetch_policy(source_id="abb", seed_url=SEED, client=failing_client())


# --- reading the rules -------------------------------------------------------


def test_a_disallowed_path_is_refused() -> None:
    policy = fetch_policy(
        source_id="abb",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /private/\n"),
    )

    assert policy.allows("https://library.abb.com/public/a.pdf") is True
    assert policy.allows("https://library.abb.com/private/a.pdf") is False


def test_a_rule_naming_our_agent_takes_precedence() -> None:
    # A source that singles us out has made a deliberate decision, and the
    # wildcard group is not the one that applies.
    policy = fetch_policy(
        source_id="abb",
        seed_url=SEED,
        client=client_returning(
            200,
            f"User-agent: *\nAllow: /\n\nUser-agent: {USER_AGENT}\nDisallow: /\n",
        ),
    )

    assert policy.allows(SEED) is False


def test_a_crawl_delay_is_read() -> None:
    policy = fetch_policy(
        source_id="abb",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nAllow: /\nCrawl-delay: 7\n"),
    )

    assert policy.crawl_delay_s == 7.0


def test_no_crawl_delay_reads_as_none_not_zero() -> None:
    # Zero would mean "the source asked for no delay", which is different from
    # "the source said nothing" — the caller applies its own floor to the
    # second.
    policy = fetch_policy(
        source_id="abb", seed_url=SEED, client=client_returning(200, "User-agent: *\nAllow: /\n")
    )

    assert policy.crawl_delay_s is None


# --- the guard the crawler calls ---------------------------------------------


def test_require_allowed_passes_a_permitted_url_silently() -> None:
    policy = fetch_policy(
        source_id="abb", seed_url=SEED, client=client_returning(200, "User-agent: *\nAllow: /\n")
    )

    require_allowed(policy, source_id="abb", url=SEED)


def test_require_allowed_raises_with_the_source_and_url_attached() -> None:
    # The error carries both so the log entry names what was refused rather
    # than only that something was.
    policy = fetch_policy(
        source_id="abb",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /\n"),
    )

    with pytest.raises(RobotsDisallowedError) as caught:
        require_allowed(policy, source_id="abb", url=SEED)

    assert caught.value.source_id == "abb"
    assert caught.value.url == SEED
