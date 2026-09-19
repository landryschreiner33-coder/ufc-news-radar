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


#: Which selection each item parameter represents, and the session-state key
#: that backs it. ``st.switch_page`` does not carry query parameters across, so
#: the selection is held in session state as well and the URL parameter is kept
#: purely so a story stays linkable and bookmarkable.
ITEM_KEYS = {"story": "_radar_story", "event_id": "_radar_event", "name": "_radar_fighter"}


def clear_items(*names: str) -> None:
    """Forget the current selection(s) - used when the user navigates away."""
    for name in (names or tuple(ITEM_KEYS)):
        if name in st.query_params:
            del st.query_params[name]
        st.session_state.pop(ITEM_KEYS.get(name, ""), None)


def go(key: str, clear: tuple = ("story", "event_id", "name", "q")) -> None:
    """Switch to a registered page, dropping stale item selections."""
    for parameter in clear:
        if parameter in st.query_params:
            del st.query_params[parameter]
        st.session_state.pop(ITEM_KEYS.get(parameter, ""), None)
    page = _PAGES.get(key)
    if page is not None:
        st.switch_page(page)
    st.rerun()


def _open(parameter: str, value: Any, page_key: str) -> None:
    st.session_state[ITEM_KEYS[parameter]] = value
    st.query_params[parameter] = str(value)
    page = _PAGES.get(page_key)
    if page is not None:
        st.switch_page(page)
    st.rerun()


def open_story(story_id: int) -> None:
    _open("story", int(story_id), "research")


def open_event(event_id: int) -> None:
    _open("event_id", int(event_id), "events")


def open_fighter(name: str) -> None:
    _open("name", str(name), "fighters")


def selection(parameter: str) -> Optional[Any]:
    """The current selection: the URL parameter first, then session state."""
    value = st.query_params.get(parameter)
    if value is None:
        value = st.session_state.get(ITEM_KEYS.get(parameter, ""))
    return value


def int_param(name: str) -> Optional[int]:
    value = selection(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def param(name: str, default: Optional[str] = None) -> Optional[str]:
    value = selection(name)
    return default if value is None else str(value)


def app_origin() -> str:
    """The scheme://host[:port] this app is being served from.

    Needed because Streamlit's page router rewrites any *relative* anchor whose
    path matches one of its pages and drops the query string with it, so
    "?story=12" and "/?story=12" both arrive empty. An absolute URL is left
    alone, so item links are built from the live request URL rather than
    hard-coded.
    """
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(str(st.context.url or ""))
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except Exception:
        pass
    return ""


def item_link(**params: Any) -> str:
    """An absolute href that carries item parameters through to app.py."""
    from urllib.parse import urlencode

    query = urlencode({key: value for key, value in params.items() if value is not None})
    return f"{app_origin()}/?{query}"
