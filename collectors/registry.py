"""Adapter registry: maps a source's ``adapter`` column to its collector class.

Adding a new kind of source is two steps:
    1. write a class that extends ``BaseCollector``
    2. register it here
No other part of the application needs to change.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Type

from collectors.base import BaseCollector
from collectors.rss import RSSCollector
from collectors.ufc_events import UFCEventsCollector
from collectors.ufc_rankings import UFCRankingsCollector

ADAPTERS: Dict[str, Type[BaseCollector]] = {
    "rss": RSSCollector,
    "ufc_rankings": UFCRankingsCollector,
    "ufc_events": UFCEventsCollector,
}


def get_adapter(name: Optional[str]) -> Optional[Type[BaseCollector]]:
    return ADAPTERS.get(str(name or "rss").strip().lower())


def build_collector(source: Dict[str, Any], client: Any = None) -> Optional[BaseCollector]:
    adapter = get_adapter(source.get("adapter"))
    if adapter is None:
        return None
    return adapter(source, client=client)


def available_adapters() -> list:
    return sorted(ADAPTERS.keys())
