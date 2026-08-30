"""robots.txt fetching and evaluation.

Separate from the crawler because the decision this makes is not a detail of
any one source: a source that disallows automated access is a source we do not
crawl, and the task is explicit that this must **hard-fail that source's job
with a clear log entry, never silently skip or proceed anyway**.

The distinction matters more than it looks. "Skip quietly" and "fail loudly"
produce the same empty result set, and only one of them tells an operator that
a source they believe is being crawled has in fact been closed to us — possibly
for weeks. Silence here looks exactly like a source that simply published
nothing new.
"""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Identifies us to the sources we read. A crawler that will not say who it is
# has already decided it might not be welcome.
USER_AGENT = "PanelPilotBot"

# robots.txt is small; a source that cannot serve one in this long is a source
# having a bad day, and we would rather fail than hang a scheduled job.
FETCH_TIMEOUT_S = 10.0


class RobotsDisallowedError(Exception):
    """A source's robots.txt forbids the access this crawl requires.

    Its own type rather than a generic error because the caller has to treat it
    differently: this is not a transient failure to retry, it is a standing
    instruction to stop, and retrying is the wrong response to it.
    """

    def __init__(self, source_id: str, url: str) -> None:
        """Record which source refused which URL.

        Args:
            source_id: The source whose robots.txt disallowed the fetch.
            url: The URL that was refused.
        """
        self.source_id = source_id
        self.url = url
        super().__init__(f"robots.txt for {source_id} disallows {url}")


class RobotsUnavailableError(Exception):
    """The source's robots.txt could not be read at all.

    Also fatal for the run, and deliberately so. An unreadable robots.txt means
    we do not know what we are permitted to fetch, and "we could not check"
    must not resolve to "so we proceeded".
    """


@dataclass(frozen=True)
class RobotsPolicy:
    """What one source's robots.txt permits.

    Attributes:
        parser: The parsed rules.
        crawl_delay_s: The source's requested delay between requests, if it
            asked for one. Honoured even when it is slower than our own limit —
            a politeness ceiling we set is not a reason to ignore a floor they
            set.
    """

    parser: urllib.robotparser.RobotFileParser
    crawl_delay_s: float | None

    def allows(self, url: str) -> bool:
        """Report whether this URL may be fetched.

        Args:
            url: The absolute URL to check.

        Returns:
            ``True`` if robots.txt permits it for our user agent.
        """
        return bool(self.parser.can_fetch(USER_AGENT, url))


def robots_url_for(url: str) -> str:
    """Return the robots.txt URL governing a given URL.

    Args:
        url: Any absolute URL on the source.

    Returns:
        The robots.txt URL at that host's root.
    """
    parsed = urlparse(url)
    return urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")


def fetch_policy(*, source_id: str, seed_url: str, client: httpx.Client) -> RobotsPolicy:
    """Read and parse one source's robots.txt.

    Args:
        source_id: The source being crawled, for the log entry.
        seed_url: Any URL on the source; the host is what matters.
        client: HTTP client to fetch with.

    Returns:
        The parsed policy.

    Raises:
        RobotsUnavailableError: If robots.txt cannot be fetched or parsed.
    """
    url = robots_url_for(seed_url)
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(url)

    try:
        response = client.get(url, timeout=FETCH_TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.error("robots.unreachable", source_id=source_id, robots_url=url, error=str(exc))
        raise RobotsUnavailableError(f"could not fetch {url}: {exc}") from exc

    if response.status_code == 404:
        # No robots.txt is the one case that legitimately means "no
        # restrictions" — the standard says an absent file permits everything,
        # and treating it as a failure would lock us out of compliant sources.
        logger.info("robots.absent", source_id=source_id, robots_url=url)
        parser.parse([])
        return RobotsPolicy(parser=parser, crawl_delay_s=None)

    if response.status_code >= 400:
        # Anything else — 401, 403, 500 — means we could not read the rules.
        # A 403 on robots.txt in particular is a strong hint we are unwelcome.
        logger.error(
            "robots.unreadable",
            source_id=source_id,
            robots_url=url,
            status_code=response.status_code,
        )
        raise RobotsUnavailableError(f"{url} returned {response.status_code}")

    parser.parse(_significant_lines(response.text))
    # Wildcards are honoured here rather than left to urllib, which encodes
    # them into literals that match nothing. See `_WildcardRule`.
    _apply_wildcard_matching(parser)
    delay = parser.crawl_delay(USER_AGENT)
    return RobotsPolicy(parser=parser, crawl_delay_s=float(delay) if delay is not None else None)


def _significant_lines(body: str) -> list[str]:
    """Split robots.txt into lines, dropping blanks and comments.

    Args:
        body: The raw robots.txt text.

    Returns:
        The rule lines, with blank and comment-only lines removed.

    A blank line terminates a record in the robots standard, and Python's
    parser implements that faithfully — so a file whose ``User-agent`` line is
    followed by an empty line has every subsequent ``Disallow`` attributed to
    no agent at all, and the parser then permits everything.

    That is not hypothetical. Rittal's robots.txt is written exactly that way:
    ``User-agent:*``, a blank line, then 96 ``Disallow`` rules. Parsed
    literally it reads as "crawl anything", which is plainly not what the file
    is trying to say, and acting on that reading would mean crawling paths the
    operator has asked us not to.

    Dropping blank lines collapses the file to a single record, which is the
    conservative reading: every rule binds, and we crawl less rather than
    more. The cost is that a file deliberately scoping different rules to
    different agents by separating records has those rules merged — also the
    conservative direction, since the union of the disallow sets is what gets
    enforced.
    """
    return [
        line for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


class _WildcardRule(urllib.robotparser.RuleLine):  # type: ignore[name-defined,misc]
    """A rule line that honours ``*`` and ``$`` the way crawlers actually do.

    Python's ``RuleLine`` percent-encodes its path and then matches by
    ``startswith``, so ``*`` becomes ``%2A`` and ``$`` becomes ``%24`` and
    neither ever matches anything. A file written with wildcards therefore
    parses without error and permits everything it meant to forbid.

    That is not hypothetical either. Schneider's robots.txt disallows
    ``/*/*/documents/*``, ``/*/*/library/*`` and ``/*/*/product/download-pdf/*``
    -- every path its documents live at -- and Python reads all of them as
    literal ``%2A`` prefixes that match no real URL. Asked whether
    ``/us/en/documents/foo.pdf`` may be fetched, the stock parser answers yes.

    ``*`` and ``$`` are not in the 1994 standard; they are the de-facto
    extension every major crawler implements and RFC 9309 later standardised.
    A site writing them means them, and the conservative reading of a rule we
    do not understand is to obey it, not to ignore it.
    """

    def __init__(self, path: str, allowance: bool) -> None:
        """Build a rule, remembering the pattern before it was encoded.

        Args:
            path: The raw path pattern from robots.txt.
            allowance: Whether this rule permits or forbids.
        """
        super().__init__(path, allowance)
        # Kept alongside the encoded `self.path` rather than replacing it, so
        # anything else reading `path` sees exactly what it saw before.
        self._pattern = path

    def applies_to(self, filename: str) -> bool:
        """Report whether this rule governs a path.

        Args:
            filename: The URL path being checked.

        Returns:
            ``True`` if the rule's pattern matches.

        Falls back to the base class whenever the pattern holds no wildcard,
        so ordinary prefix rules keep their existing behaviour exactly --
        including the ``path == "*"`` special case and percent-encoding of
        non-ASCII paths.
        """
        if "*" not in self._pattern and not self._pattern.endswith("$"):
            return bool(super().applies_to(filename))
        return _matches_pattern(self._pattern, filename)


def _matches_pattern(pattern: str, path: str) -> bool:
    """Match a robots path pattern against a URL path.

    Args:
        pattern: The rule's path, which may contain ``*`` and a trailing ``$``.
        path: The URL path to test.

    Returns:
        ``True`` if the pattern matches.

    Implemented directly rather than by translating to a regex. The obvious
    translation is ``fnmatch``, which is wrong here in two ways: its ``*`` does
    not cross ``/`` in some implementations, and its ``[...]`` and ``?`` are
    metacharacters that robots.txt treats as literal path characters -- a rule
    for ``/search?q=`` would silently become a single-character wildcard.
    """
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]

    segments = pattern.split("*")

    # The text before the first `*` is a prefix, not a floating match.
    first = segments[0]
    if not path.startswith(first):
        return False

    position = len(first)
    for segment in segments[1:-1]:
        if not segment:
            # Consecutive stars collapse; `**` means what `*` means.
            continue
        found = path.find(segment, position)
        if found == -1:
            return False
        position = found + len(segment)

    last = segments[-1] if len(segments) > 1 else ""
    if not last:
        # A trailing `*` matches the rest, but `$` after it still requires the
        # pattern to have consumed everything -- which a bare `*` always does.
        return True

    if anchored:
        # The final literal has to sit at the very end, and not overlap the
        # text already consumed by earlier segments.
        return path.endswith(last) and len(path) - len(last) >= position

    return path.find(last, position) != -1


def _apply_wildcard_matching(parser: urllib.robotparser.RobotFileParser) -> None:
    """Replace every parsed rule with one that understands wildcards.

    Args:
        parser: A parser that has already read a robots.txt body.

    Done after parsing rather than by subclassing the parser, because the
    parser builds ``RuleLine`` objects by name inside ``parse``; there is no
    seam to inject a different class. Rewriting the rules it produced leaves
    its agent matching, precedence and crawl-delay handling untouched, which
    is the part that is already correct.
    """
    # `entries` and `default_entry` exist on the stdlib parser but are absent
    # from typeshed's stubs, which declare only the public methods.
    entries = list(parser.entries)  # type: ignore[attr-defined]
    default_entry = parser.default_entry  # type: ignore[attr-defined]
    if default_entry is not None:
        entries.append(default_entry)

    for entry in entries:
        entry.rulelines = [
            _WildcardRule(_original_pattern(rule), rule.allowance) for rule in entry.rulelines
        ]


def _original_pattern(rule: urllib.robotparser.RuleLine) -> str:  # type: ignore[name-defined]
    """Recover the pattern a rule was written with.

    Args:
        rule: A parsed rule line.

    Returns:
        The path with ``*`` and ``$`` restored.

    ``RuleLine.__init__`` runs ``quote`` over the path, so by the time a rule
    exists its wildcards are already ``%2A`` and ``%24``. Unquoting the whole
    path would also decode any legitimately-encoded character the site wrote
    deliberately -- a literal ``%2A`` in a filename would become a wildcard --
    so only the two metacharacters are restored, and only from the exact
    uppercase spelling ``quote`` emits.
    """
    path: str = rule.path
    return path.replace("%2A", "*").replace("%24", "$")


def require_allowed(policy: RobotsPolicy, *, source_id: str, url: str) -> None:
    """Fail the run if this URL may not be fetched.

    Args:
        policy: The source's parsed robots.txt.
        source_id: The source being crawled, for the log entry.
        url: The URL about to be fetched.

    Raises:
        RobotsDisallowedError: If robots.txt forbids it.
    """
    if policy.allows(url):
        return
    logger.error("robots.disallowed", source_id=source_id, url=url)
    raise RobotsDisallowedError(source_id, url)
