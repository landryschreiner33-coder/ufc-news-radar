"""A tiny page registry.

``st.switch_page`` needs the ``st.Page`` object, which lives in ``app.py``.
Pages cannot import ``app`` (that would be circular), so ``app`` registers its
pages here at start-up and any page can ask for one by key.

Everything degrades gracefully: if a key is missing (during tests, or when a
page module is imported on its own) the helpers clear the relevant query
parameters and rerun instead of raising.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st

_PAGES: Dict[str, Any] = {}


def register(pages: Dict[str, Any]) -> None:
    _PAGES.clear()
    _PAGES.update(pages)


def get(key: str) -> Optional[Any]:
    return _PAGES.get(key)


def go(key: str, clear: tuple = ("story", "event_id", "name", "q")) -> None:
    """Switch to a registered page, dropping stale item parameters."""
    for parameter in clear:
        if parameter in st.query_params:
            del st.query_params[parameter]
    page = _PAGES.get(key)
    if page is not None:
        st.switch_page(page)
    st.rerun()


def open_story(story_id: int) -> None:
    st.query_params["story"] = str(int(story_id))
    page = _PAGES.get("research")
    if page is not None:
        st.switch_page(page)
    st.rerun()


def open_event(event_id: int) -> None:
    st.query_params["event_id"] = str(int(event_id))
    page = _PAGES.get("events")
    if page is not None:
        st.switch_page(page)
    st.rerun()


def open_fighter(name: str) -> None:
    st.query_params["name"] = str(name)
    page = _PAGES.get("fighters")
    if page is not None:
        st.switch_page(page)
    st.rerun()


def int_param(name: str) -> Optional[int]:
    value = st.query_params.get(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def param(name: str, default: Optional[str] = None) -> Optional[str]:
    return st.query_params.get(name, default)
