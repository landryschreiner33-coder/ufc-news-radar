"""A fake HTTP client for tests: serves local fixtures and simulates failures.

Collectors take an ``HttpClient``-shaped object, so tests can exercise the real
collection code path without touching the network.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from utils.http import HttpResult

FIXTURES = Path(__file__).parent / "fixtures"


class FakeHttpClient:
    """Maps URL substrings to fixture files (or to simulated failures).

    ``routes`` values may be:
      * a path relative to tests/fixtures  -> served as a 200 response
      * an ``HttpResult``                  -> returned as-is
      * a callable(url) -> HttpResult
    """

    def __init__(self, routes: Optional[Dict[str, Any]] = None, default: Any = None) -> None:
        self.routes = routes or {}
        self.default = default
        self.calls: list = []

    # --- the HttpClient surface the collectors use -------------------------
    def get(self, url: str, **kwargs: Any) -> HttpResult:
        self.calls.append(url)
        target = self._resolve(url)
        if target is None:
            return HttpResult(url=url, error="no route configured in FakeHttpClient",
                              error_kind="connection")
        return target

    def get_json(self, url: str, **kwargs: Any) -> HttpResult:
        return self.get(url, **kwargs)

    def get_conditional(self, url: str, cache_key: Optional[str] = None, **kwargs: Any) -> HttpResult:
        return self.get(url, **kwargs)

    # ----------------------------------------------------------------------
    def _resolve(self, url: str) -> Optional[HttpResult]:
        for pattern, target in self.routes.items():
            if pattern in url:
                return _materialise(target, url)
        if self.default is not None:
            return _materialise(self.default, url)
        return None


def _materialise(target: Any, url: str) -> HttpResult:
    if callable(target):
        return target(url)
    if isinstance(target, HttpResult):
        return target
    path = Path(target)
    if not path.is_absolute():
        path = FIXTURES / path
    data = path.read_bytes()
    return HttpResult(ok=True, status_code=200, url=url, content=data,
                      text=data.decode("utf-8", errors="replace"))


def failure(kind: str = "connection", message: str = "simulated network failure") -> HttpResult:
    return HttpResult(ok=False, url="", error=message, error_kind=kind)


def http_error(status: int = 500) -> HttpResult:
    return HttpResult(ok=False, status_code=status, url="", error=f"HTTP {status}", error_kind="http")
