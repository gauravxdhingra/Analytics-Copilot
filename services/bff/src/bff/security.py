"""Browser-facing protections: CSRF, origin checks, rate limiting and response headers."""

import hmac
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Request

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def csrf_valid(request: Request, expected: str, cookie_name: str) -> bool:
    """Double-submit check: the header must equal both the cookie and the session's token."""
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(cookie_name, "")
    return (
        bool(header)
        and hmac.compare_digest(header, expected)
        and hmac.compare_digest(cookie, expected)
    )


def origin_valid(request: Request, public_url: str) -> bool:
    """State-changing requests must come from our own origin."""
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer", "")
        return referer == public_url or referer.startswith(public_url + "/")
    return origin == public_url


class RateLimiter:
    """A sliding window per key. In memory: fine for one process, shared store when scaled."""

    def __init__(
        self, per_minute: int, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._limit = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock().timestamp()
        hits = self._hits[key]
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
