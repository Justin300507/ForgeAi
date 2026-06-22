"""
Live Deployment Metrics Collector (V12)

Hits a deployed URL repeatedly and collects:
  - availability (% successful requests)
  - avg / p95 response time
  - error rate (5xx responses)
  - slowest endpoint
"""
import time
import statistics
from typing import Any

import requests


def collect_metrics(
    url: str,
    endpoints: list[str] | None = None,
    requests_per_endpoint: int = 5,
    timeout: int = 10,
) -> dict[str, Any]:
    """
    Probe a live URL across several endpoints and return a metrics report.

    Args:
        url:                    Base URL (e.g. https://app.up.railway.app)
        endpoints:              Paths to probe (defaults to ["/", "/health", "/docs"])
        requests_per_endpoint:  How many requests per path
        timeout:                Per-request timeout in seconds

    Returns:
        {
            "availability": 0.0–1.0,
            "avg_response_ms": float,
            "p95_response_ms": float,
            "error_rate": 0.0–1.0,
            "endpoint_stats": { "/path": {...}, ... },
            "issues": ["description", ...],
        }
    """
    url = url.rstrip("/")
    endpoints = endpoints or ["/", "/health", "/docs"]

    all_times: list[float] = []
    total = 0
    errors = 0
    endpoint_stats: dict[str, Any] = {}

    for path in endpoints:
        times: list[float] = []
        path_errors = 0

        for _ in range(requests_per_endpoint):
            total += 1
            try:
                resp = requests.get(f"{url}{path}", timeout=timeout, allow_redirects=True)
                ms = resp.elapsed.total_seconds() * 1000
                times.append(ms)
                if resp.status_code >= 500:
                    path_errors += 1
                    errors += 1
            except Exception:
                path_errors += 1
                errors += 1
                times.append(timeout * 1000)  # count timeout as max latency

        if times:
            all_times.extend(times)
            endpoint_stats[path] = {
                "avg_ms": round(statistics.mean(times), 1),
                "min_ms": round(min(times), 1),
                "max_ms": round(max(times), 1),
                "error_rate": round(path_errors / len(times), 3),
            }

    availability = round((total - errors) / total, 3) if total else 0.0
    error_rate = round(errors / total, 3) if total else 0.0
    avg_ms = round(statistics.mean(all_times), 1) if all_times else 0.0
    p95_ms = round(sorted(all_times)[int(len(all_times) * 0.95)], 1) if len(all_times) >= 2 else avg_ms

    issues: list[str] = []
    if availability < 0.9:
        issues.append(f"Low availability: {availability:.0%}")
    if error_rate > 0.1:
        issues.append(f"High error rate: {error_rate:.0%}")
    if avg_ms > 2000:
        issues.append(f"Slow responses: avg {avg_ms:.0f}ms")
    if p95_ms > 5000:
        issues.append(f"P95 latency critical: {p95_ms:.0f}ms")

    # Find the slowest endpoint
    slowest = max(endpoint_stats.items(), key=lambda kv: kv[1]["avg_ms"], default=(None, {}))

    return {
        "url": url,
        "availability": availability,
        "avg_response_ms": avg_ms,
        "p95_response_ms": p95_ms,
        "error_rate": error_rate,
        "total_requests": total,
        "failed_requests": errors,
        "endpoint_stats": endpoint_stats,
        "slowest_endpoint": slowest[0],
        "issues": issues,
        "healthy": len(issues) == 0,
    }
