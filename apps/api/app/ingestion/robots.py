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

    parser.parse(response.text.splitlines())
    delay = parser.crawl_delay(USER_AGENT)
    return RobotsPolicy(parser=parser, crawl_delay_s=float(delay) if delay is not None else None)


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
