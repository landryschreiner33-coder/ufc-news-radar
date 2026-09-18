"""A small, forgiving HTTP client.

Rules this module enforces for the whole app:
  * every request has a timeout;
  * network problems return a result object, they never raise;
  * transient failures (connection reset, 5xx) are retried with backoff;
  * 429 responses are reported as rate limits, with the server's reset hint;
  * conditional requests (ETag / Last-Modified) avoid re-downloading feeds
    that have not changed.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests

from utils.config import get_config
from utils.logging_setup import get_logger
from utils.paths import CACHE_DIR, ensure_dirs
from utils.textutil import sha1

logger = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 500, 502, 503, 504}


@dataclass
class HttpResult:
    """Outcome of a single HTTP request. ``ok`` means 'usable response body'."""

    ok: bool = False
    status_code: Optional[int] = None
    url: str = ""
    text: str = ""
    content: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    error_kind: Optional[str] = None  # timeout | connection | http | rate_limit | parse | not_modified
    elapsed_ms: int = 0
    from_cache: bool = False
    rate_limit_reset_epoch: Optional[int] = None

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    @property
    def rate_limited(self) -> bool:
        return self.error_kind == "rate_limit"

    def json(self) -> Any:
        try:
            return json.loads(self.text) if self.text else None
        except json.JSONDecodeError as exc:
            logger.debug("JSON decode failed for %s: %s", self.url, exc)
            return None


class HttpClient:
    """Shared ``requests`` session with retries and sane defaults."""

    def __init__(
        self,
        timeout: Optional[int] = None,
        user_agent: Optional[str] = None,
        max_retries: int = 2,
        backoff_seconds: float = 1.5,
    ) -> None:
        config = get_config()
        self.timeout = timeout or config.http_timeout
        self.user_agent = user_agent or config.user_agent
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        allow_redirects: bool = True,
    ) -> HttpResult:
        attempts = (max_retries if max_retries is not None else self.max_retries) + 1
        last: HttpResult = HttpResult(url=url, error="not attempted", error_kind="connection")
        for attempt in range(attempts):
            last = self._attempt(url, headers, params, timeout, allow_redirects)
            retryable = (
                last.error_kind in ("timeout", "connection")
                or (last.status_code in RETRYABLE_STATUS if last.status_code else False)
            )
            if last.ok or last.not_modified or not retryable or attempt == attempts - 1:
                return last
            delay = self.backoff_seconds * (2 ** attempt) + random.uniform(0, 0.4)
            logger.debug("Retrying %s in %.1fs (%s)", url, delay, last.error)
            time.sleep(delay)
        return last

    def _attempt(
        self,
        url: str,
        headers: Optional[Dict[str, str]],
        params: Optional[Dict[str, Any]],
        timeout: Optional[int],
        allow_redirects: bool,
    ) -> HttpResult:
        started = time.monotonic()
        try:
            response = self.session.get(
                url,
                headers=headers or {},
                params=params or None,
                timeout=timeout or self.timeout,
                allow_redirects=allow_redirects,
            )
        except requests.exceptions.Timeout as exc:
            return HttpResult(url=url, error=f"timeout after {timeout or self.timeout}s: {exc}",
                              error_kind="timeout", elapsed_ms=_ms(started))
        except requests.exceptions.SSLError as exc:
            return HttpResult(url=url, error=f"TLS error: {exc}", error_kind="connection",
                              elapsed_ms=_ms(started))
        except requests.exceptions.ConnectionError as exc:
            return HttpResult(url=url, error=f"connection error: {_short(exc)}",
                              error_kind="connection", elapsed_ms=_ms(started))
        except requests.exceptions.RequestException as exc:
            return HttpResult(url=url, error=f"request failed: {_short(exc)}",
                              error_kind="connection", elapsed_ms=_ms(started))
        except Exception as exc:  # a broken proxy/DNS stack must not kill a run
            return HttpResult(url=url, error=f"unexpected error: {_short(exc)}",
                              error_kind="connection", elapsed_ms=_ms(started))

        headers_out = {k.lower(): v for k, v in response.headers.items()}
        result = HttpResult(
            status_code=response.status_code,
            url=str(response.url),
            headers=headers_out,
            elapsed_ms=_ms(started),
        )
        if response.status_code == 304:
            result.error_kind = "not_modified"
            result.error = "not modified"
            return result
        if response.status_code == 429:
            result.error_kind = "rate_limit"
            result.error = "rate limited by server (HTTP 429)"
            result.rate_limit_reset_epoch = _reset_epoch(headers_out)
            return result
        if response.status_code >= 400:
            result.error_kind = "http"
            result.error = f"HTTP {response.status_code}"
            result.text = response.text[:2000] if response.text else ""
            return result
        try:
            result.content = response.content
            result.text = response.text
        except Exception as exc:
            result.error_kind = "parse"
            result.error = f"could not read body: {_short(exc)}"
            return result
        result.ok = True
        return result

    def post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        max_retries: int = 1,
    ) -> HttpResult:
        """POST JSON (used by the AI providers). Never raises."""
        request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        request_headers.update(headers or {})
        attempts = max_retries + 1
        last = HttpResult(url=url, error="not attempted", error_kind="connection")
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                response = self.session.post(
                    url, json=payload, headers=request_headers, timeout=timeout or self.timeout
                )
            except requests.exceptions.Timeout as exc:
                last = HttpResult(url=url, error=f"timeout: {_short(exc)}", error_kind="timeout",
                                  elapsed_ms=_ms(started))
            except requests.exceptions.RequestException as exc:
                last = HttpResult(url=url, error=f"request failed: {_short(exc)}",
                                  error_kind="connection", elapsed_ms=_ms(started))
            except Exception as exc:
                last = HttpResult(url=url, error=f"unexpected error: {_short(exc)}",
                                  error_kind="connection", elapsed_ms=_ms(started))
            else:
                headers_out = {k.lower(): v for k, v in response.headers.items()}
                last = HttpResult(status_code=response.status_code, url=str(response.url),
                                  headers=headers_out, elapsed_ms=_ms(started))
                if response.status_code == 429:
                    last.error_kind = "rate_limit"
                    last.error = "rate limited by provider (HTTP 429)"
                    last.rate_limit_reset_epoch = _reset_epoch(headers_out)
                elif response.status_code >= 400:
                    last.error_kind = "http"
                    last.error = f"HTTP {response.status_code}: {(response.text or '')[:300]}"
                else:
                    last.ok = True
                    last.text = response.text
                    last.content = response.content
            retryable = last.error_kind in ("timeout", "connection") or (
                last.status_code in RETRYABLE_STATUS if last.status_code else False
            )
            if last.ok or not retryable or attempt == attempts - 1:
                return last
            time.sleep(self.backoff_seconds * (2 ** attempt))
        return last

    def get_json(self, url: str, **kwargs: Any) -> HttpResult:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Accept", "application/json")
        return self.get(url, headers=headers, **kwargs)

    def get_conditional(self, url: str, cache_key: Optional[str] = None, **kwargs: Any) -> HttpResult:
        """GET with ETag/Last-Modified reuse so unchanged feeds cost nothing."""
        ensure_dirs()
        key = sha1(cache_key or url)
        meta_file = CACHE_DIR / f"{key}.meta.json"
        headers = dict(kwargs.pop("headers", {}) or {})
        meta: Dict[str, str] = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]
        result = self.get(url, headers=headers, **kwargs)
        if result.ok:
            new_meta = {
                "etag": result.headers.get("etag", ""),
                "last_modified": result.headers.get("last-modified", ""),
            }
            if any(new_meta.values()):
                try:
                    meta_file.write_text(json.dumps(new_meta), encoding="utf-8")
                except OSError:
                    pass
        return result


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _short(exc: Exception, limit: int = 200) -> str:
    text = str(exc).replace("\n", " ")
    return text[:limit]


def _reset_epoch(headers: Dict[str, str]) -> Optional[int]:
    for key in ("x-rate-limit-reset", "x-ratelimit-reset", "ratelimit-reset"):
        if key in headers:
            try:
                return int(float(headers[key]))
            except (TypeError, ValueError):
                continue
    if "retry-after" in headers:
        try:
            return int(time.time()) + int(float(headers["retry-after"]))
        except (TypeError, ValueError):
            return None
    return None


_DEFAULT_CLIENT: Optional[HttpClient] = None


def get_client() -> HttpClient:
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = HttpClient()
    return _DEFAULT_CLIENT
