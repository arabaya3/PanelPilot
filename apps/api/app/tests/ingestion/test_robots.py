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


def test_a_blank_line_after_the_user_agent_does_not_discard_the_rules() -> None:
    """A stray blank line must not turn a restrictive file into a permissive one.

    A blank line terminates a record in the robots standard, and Python's
    parser implements that faithfully — so this shape attributes every
    `Disallow` to no agent and permits everything.

    Not hypothetical: Rittal's robots.txt is written exactly this way,
    `User-agent:*` followed by an empty line and then 96 `Disallow` rules.
    Reading it literally would mean crawling paths the operator explicitly
    asked us not to, while believing we were compliant.
    """
    body = "User-agent:*\r\n\r\nDisallow: /products/show/\r\nDisallow: /private/\r\n"
    with client_returning(200, body) as client:
        policy = fetch_policy(source_id="s", seed_url=SEED, client=client)

    with pytest.raises(RobotsDisallowedError):
        require_allowed(policy, source_id="s", url="https://example.invalid/products/show/x")
    with pytest.raises(RobotsDisallowedError):
        require_allowed(policy, source_id="s", url="https://example.invalid/private/y")

    # Anything not named is still permitted.
    require_allowed(policy, source_id="s", url="https://example.invalid/apps/download/")


def test_comment_only_lines_are_ignored() -> None:
    """Comments must not be mistaken for rules or terminate a record."""
    body = "User-agent: *\n# a comment\nDisallow: /nope/\n"
    with client_returning(200, body) as client:
        policy = fetch_policy(source_id="s", seed_url=SEED, client=client)

    with pytest.raises(RobotsDisallowedError):
        require_allowed(policy, source_id="s", url="https://example.invalid/nope/x")
    require_allowed(policy, source_id="s", url="https://example.invalid/yes/x")


# --- wildcards, which the stock parser silently ignores ----------------------
#
# Python's `RuleLine` percent-encodes its path and matches by `startswith`, so
# `*` becomes `%2A` and matches nothing. A file written with wildcards parses
# without error and permits everything it meant to forbid.
#
# Schneider's robots.txt is written exactly that way: `/*/*/documents/*`,
# `/*/*/library/*` and `/*/*/product/download-pdf/*` — every path its documents
# live at. Asked whether `/us/en/documents/foo.pdf` may be fetched, the stock
# parser answers yes. These are the tests that make the fix falsifiable.


def test_a_wildcard_rule_disallows_a_matching_path() -> None:
    """The defect, in one assertion.

    Without wildcard handling this returns True and we crawl a path the
    operator has asked us not to.
    """
    policy = fetch_policy(
        source_id="schneider",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /*/*/documents/*\n"),
    )

    assert policy.allows("https://library.abb.com/us/en/documents/foo.pdf") is False


def test_a_wildcard_rule_still_permits_a_non_matching_path() -> None:
    """The other half: the fix must not over-block.

    A rule that forbade everything would also "pass" the test above, and would
    quietly stop every crawl in the system.
    """
    policy = fetch_policy(
        source_id="schneider",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /*/*/documents/*\n"),
    )

    assert policy.allows("https://library.abb.com/us/en/about-us/") is True


def test_a_leading_wildcard_matches_anywhere() -> None:
    """`Disallow: /*.pdf` is a common spelling and means any PDF."""
    policy = fetch_policy(
        source_id="s",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /*.pdf\n"),
    )

    assert policy.allows("https://library.abb.com/a/b/manual.pdf") is False
    assert policy.allows("https://library.abb.com/a/b/manual.html") is True


def test_a_trailing_wildcard_matches_the_rest() -> None:
    policy = fetch_policy(
        source_id="s",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /docs/*\n"),
    )

    assert policy.allows("https://library.abb.com/docs/anything/at/all") is False


def test_an_end_anchor_matches_only_at_the_end() -> None:
    """`$` is the other metacharacter urllib encodes into a literal.

    `Disallow: /*.pdf$` forbids a PDF and must not forbid a path that merely
    contains `.pdf` before a query string or a longer segment.
    """
    policy = fetch_policy(
        source_id="s",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /*.pdf$\n"),
    )

    assert policy.allows("https://library.abb.com/a/manual.pdf") is False
    assert policy.allows("https://library.abb.com/a/manual.pdf.html") is True


def test_a_plain_prefix_rule_is_unchanged() -> None:
    """The fix must not alter rules that never had a wildcard.

    Those go through the base class untouched, which is what keeps this a
    contained change rather than a reimplementation of the whole matcher.
    """
    policy = fetch_policy(
        source_id="s",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /private/\n"),
    )

    assert policy.allows("https://library.abb.com/private/a.pdf") is False
    assert policy.allows("https://library.abb.com/public/a.pdf") is True


def test_a_wildcard_allow_rule_matches_when_it_is_reached() -> None:
    """Wildcards appear in `Allow` too, so those rules must match as well.

    Stated without a competing earlier `Disallow`, deliberately. Python
    resolves a conflict by taking the FIRST matching rule rather than the most
    specific one, so a later `Allow` never overrides an earlier `Disallow` --
    with or without wildcards, and regardless of this fix. That is a separate
    defect in the stock parser, out of scope here and recorded rather than
    smuggled into a wildcard change; a test asserting the carve-out wins would
    be asserting behaviour the parser does not have.
    """
    body = "User-agent: *\nAllow: /docs/*/public/\nDisallow: /docs/\n"
    policy = fetch_policy(source_id="s", seed_url=SEED, client=client_returning(200, body))

    assert policy.allows("https://library.abb.com/docs/a/public/x.pdf") is True
    assert policy.allows("https://library.abb.com/docs/a/private/x.pdf") is False


def test_a_literal_percent_encoded_star_is_not_treated_as_a_wildcard() -> None:
    """A site may legitimately have `%2A` in a path.

    Only the spelling `quote` itself emits is restored, so a path the operator
    wrote encoded stays literal rather than silently becoming a wildcard.
    """
    policy = fetch_policy(
        source_id="s",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /files/star/\n"),
    )

    assert policy.allows("https://library.abb.com/files/star/a.pdf") is False
    assert policy.allows("https://library.abb.com/files/other/a.pdf") is True


def test_consecutive_wildcards_collapse() -> None:
    """`**` is not special; it means what `*` means."""
    policy = fetch_policy(
        source_id="s",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /a**b/\n"),
    )

    assert policy.allows("https://library.abb.com/axxb/c.pdf") is False


def test_the_schneider_pattern_end_to_end() -> None:
    """The real file's shape, since that is what motivated the fix.

    Three document paths forbidden, one unrelated path still allowed — the
    combination that proves the rules bind without over-blocking.
    """
    body = (
        "User-agent: *\n"
        "Disallow: /*/*/documents/*\n"
        "Disallow: /*/*/library/*\n"
        "Disallow: /*/*/product/download-pdf/*\n"
    )
    policy = fetch_policy(source_id="schneider", seed_url=SEED, client=client_returning(200, body))

    assert policy.allows("https://library.abb.com/us/en/documents/x.pdf") is False
    assert policy.allows("https://library.abb.com/ww/en/library/x.pdf") is False
    assert policy.allows("https://library.abb.com/us/en/product/download-pdf/ABC") is False
    assert policy.allows("https://library.abb.com/us/en/about-us/") is True


def test_the_text_before_the_first_wildcard_is_a_prefix_not_a_search() -> None:
    """`Disallow: /docs/*` governs paths that START with `/docs/`.

    Treating that leading text as a floating match instead would make the rule
    also cover `/other/docs/`, silently blocking paths the operator never
    restricted — over-blocking is quieter than under-blocking and just as
    wrong. A mutation removing the anchor survived every other test here.
    """
    policy = fetch_policy(
        source_id="s",
        seed_url=SEED,
        client=client_returning(200, "User-agent: *\nDisallow: /docs/*\n"),
    )

    assert policy.allows("https://library.abb.com/docs/a.pdf") is False
    assert policy.allows("https://library.abb.com/other/docs/a.pdf") is True
