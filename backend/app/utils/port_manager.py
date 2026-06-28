"""
Thread-safe port allocator for concurrent runtime validation.

When 10 projects run in parallel, each needs its own uvicorn instance
on a different port. This module manages allocation and release.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_in_use: set[int] = set()
_BASE_PORT = 8100  # 8001 stays for single-run; parallel uses 8100+


def acquire_port() -> int:
    """
    Claim the next available port >= _BASE_PORT.
    Thread-safe. Raises RuntimeError if > 50 ports are in use.
    """
    with _lock:
        for candidate in range(_BASE_PORT, _BASE_PORT + 50):
            if candidate not in _in_use:
                _in_use.add(candidate)
                return candidate
    raise RuntimeError("Port pool exhausted — too many concurrent validations")


def release_port(port: int) -> None:
    """Release a previously acquired port back to the pool."""
    with _lock:
        _in_use.discard(port)


class ManagedPort:
    """Context manager: acquires a port on enter, releases on exit."""

    def __init__(self):
        self.port: int = 0

    def __enter__(self) -> int:
        self.port = acquire_port()
        return self.port

    def __exit__(self, *_):
        release_port(self.port)
