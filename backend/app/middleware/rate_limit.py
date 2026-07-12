"""
Exp070: simple in-memory rate limiter for ForgeAI's own API endpoints.

No external dependency (no slowapi/redis) -- per this experiment's
"keep implementation simple" instruction and $0 API budget. Keyed by
client IP address + a named bucket, so /login and /project/v15 have
independent limits even from the same IP, and a fixed window per key.

Known, accepted limitation (documented, not hidden): this store is a
single-process, in-memory dict. It does NOT coordinate across multiple
uvicorn worker processes -- each process enforces its own limit
independently, so the effective limit under N worker processes is
roughly N times the configured value. ForgeAI's current deployment
model runs a single backend process (per this project's own
docs/SYSTEM_DESIGN.md), so this is an acceptable tradeoff for a $0,
dependency-free fix; a multi-process deployment would need a shared
store (e.g. Redis) instead -- exactly the kind of change this
experiment's rules say not to build here.

Also does not evict stale (bucket, ip) keys from memory over time --
acceptable for now given the bounded set of protected endpoints and
the low cardinality of real client IPs in this project's current
deployment scale; flagged as a future improvement, not fixed here.
"""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

_buckets: dict[str, list[float]] = defaultdict(list)


def _client_key(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


def rate_limit(max_requests: int, window_seconds: int, bucket: str):
    """
    Returns a FastAPI dependency enforcing `max_requests` per
    `window_seconds` per client IP, within the named `bucket`. Attach
    via `dependencies=[Depends(rate_limit(...))]` on a route decorator.
    """
    def _check(request: Request) -> None:
        now = time.time()
        key = f"{bucket}:{_client_key(request)}"
        timestamps = _buckets[key]
        cutoff = now - window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)
        if len(timestamps) >= max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded for this action "
                    f"(max {max_requests} requests per {window_seconds}s). "
                    f"Please try again later."
                ),
                headers={"Retry-After": str(window_seconds)},
            )
        timestamps.append(now)

    return _check


def reset_all_rate_limits() -> None:
    """Test-only helper: clear all rate-limit state between test runs."""
    _buckets.clear()
