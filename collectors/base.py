"""Collector interface.

Every source adapter implements three methods:

    collect()   -> fetch raw items from the source (may raise)
    normalize() -> turn one raw item into the app's article shape
    validate()  -> decide whether a normalised item is usable

``run()`` wraps all three so that *any* failure in one source - network,
parsing, or a bug in an adapter - is caught, recorded against that source and
returned as a result object.  A broken source can never stop a collection run
or crash the app.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.http import HttpClient, get_client
from utils.logging_setup import get_logger
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)


@dataclass
class CollectorResult:
    """Outcome of one source run."""

    source_key: str
    ok: bool = False
    items: List[Dict[str, Any]] = field(default_factory=list)
    raw_count: int = 0
    rejected_count: int = 0
    error: Optional[str] = None
    error_kind: Optional[str] = None
    resolved_url: Optional[str] = None
    duration_ms: int = 0
    kind: str = "articles"          # articles | rankings | events
    payload: Dict[str, Any] = field(default_factory=dict)  # adapter-specific extras

    @property
    def count(self) -> int:
        return len(self.items)


class CollectorError(Exception):
    """Raised by adapters for an expected, reportable failure."""

    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


class BaseCollector:
    """Base class for all source adapters."""

    adapter = "base"
    kind = "articles"

    def __init__(self, source: Dict[str, Any], client: Optional[HttpClient] = None) -> None:
        self.source = source
        self.client = client or get_client()
        self.resolved_url: Optional[str] = None

    # ------------------------------------------------------------- hooks --
    def collect(self) -> List[Any]:
        """Fetch and return raw items. Subclasses must implement."""
        raise NotImplementedError

    def normalize(self, raw: Any) -> Optional[Dict[str, Any]]:
        """Convert one raw item into a dict the pipeline understands."""
        raise NotImplementedError

    def validate(self, item: Dict[str, Any]) -> bool:
        """Reject unusable items (no link, no title, obviously off-topic)."""
        if not item:
            return False
        url = str(item.get("url") or "")
        title = str(item.get("title") or "").strip()
        return url.startswith("http") and len(title) >= 8

    # -------------------------------------------------------------- runner --
    def run(self) -> CollectorResult:
        import time

        started = time.monotonic()
        result = CollectorResult(source_key=self.source.get("key", self.adapter), kind=self.kind)
        try:
            raw_items = self.collect()
        except CollectorError as exc:
            result.error, result.error_kind = str(exc), exc.kind
            result.duration_ms = int((time.monotonic() - started) * 1000)
            logger.warning("Source %s failed: %s", result.source_key, exc)
            return result
        except Exception as exc:  # adapter bug - isolate it, keep the run alive
            result.error = f"unexpected adapter error: {exc}"
            result.error_kind = "adapter"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            logger.error("Source %s raised: %s\n%s", result.source_key, exc, traceback.format_exc())
            return result

        result.raw_count = len(raw_items)
        for raw in raw_items:
            try:
                item = self.normalize(raw)
            except Exception as exc:  # one malformed entry must not lose the feed
                logger.debug("Skipping malformed item from %s: %s", result.source_key, exc)
                result.rejected_count += 1
                continue
            if item is None or not self.validate(item):
                result.rejected_count += 1
                continue
            item.setdefault("collected_at", utcnow_iso())
            result.items.append(item)

        result.ok = True
        result.resolved_url = self.resolved_url
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result
