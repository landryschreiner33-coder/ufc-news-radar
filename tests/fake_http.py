"""A fake HTTP client for tests: serves local fixtures and simulates failures.

Collectors take an ``HttpClient``-shaped object, so tests can exercise the real
collection code path without touching the network.
"""
from __future__ import annotations

import re
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Dict, Optional

from utils.http import HttpResult
from utils.timeutil import utcnow

FIXTURES = Path(__file__).parent / "fixtures"

#: ``{{minutes_ago:145}}`` in a fixture becomes an RFC-822 date that many
#: minutes before now.
#:
#: Feed fixtures used to carry real dates, which quietly rotted: everything
#: passed until the fixtures aged past the pipeline's freshness windows, and
#: then a test that had nothing to do with time started failing on a Tuesday
#: for no reason anyone could see. A fixture that says "two hours old" means
#: the same thing on every day it is ever run.
_PLACEHOLDER_RE = re.compile(r"\{\{minutes_ago:(\d+(?:\.\d+)?)\}\}")


def render_fixture(text: str) -> str:
    """Substitute the time placeholders in a fixture's text."""
    def replace(match: "re.Match[str]") -> str:
        moment = utcnow() - timedelta(minutes=float(match.group(1)))
        return format_datetime(moment)

    return _PLACEHOLDER_RE.sub(replace, text)


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
    if b"{{" in data:
        data = render_fixture(data.decode("utf-8", errors="replace")).encode("utf-8")
    return HttpResult(ok=True, status_code=200, url=url, content=data,
                      text=data.decode("utf-8", errors="replace"))


def failure(kind: str = "connection", message: str = "simulated network failure") -> HttpResult:
    return HttpResult(ok=False, url="", error=message, error_kind=kind)


def http_error(status: int = 500) -> HttpResult:
    return HttpResult(ok=False, status_code=status, url="", error=f"HTTP {status}", error_kind="http")
