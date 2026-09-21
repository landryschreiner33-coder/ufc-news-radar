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
#: the selection is held in session state as well and the URL parameter is
#: written back by ``sync_url`` once the target page is running - that is what
#: makes a story, event or fighter view linkable, bookmarkable and refreshable.
ITEM_KEYS = {
    "story": "_radar_story",
    "event_id": "_radar_event",
    "name": "_radar_fighter",
    "q": "_radar_query",
}

#: (parameter, pages that own it, the page key to open it on). Used by the
#: router in ``app.py`` so the ownership rules live next to the keys.
ITEM_OWNERS = (
    ("story", ("research", "tiktok-studio"), "research"),
    ("event_id", ("events",), "events"),
    ("name", ("fighters",), "fighters"),
    ("q", ("search",), "search"),
)


def set_selection(parameter: str, value: Any) -> None:
    """Record what the page is currently showing, for ``sync_url``."""
    key = ITEM_KEYS.get(parameter)
    if not key:
        return
    if value in (None, ""):
        st.session_state.pop(key, None)
    else:
        st.session_state[key] = value


def remember(parameter: str) -> None:
    """Copy a URL parameter into session state so it survives a page switch.

    Needed for a link that arrives on the wrong page - ``/?story=4`` - because
    ``st.switch_page`` drops the query string on the way to the page that owns
    the parameter, and the selection would be lost between the two.
    """
    value = st.query_params.get(parameter)
    if value is not None:
        st.session_state[ITEM_KEYS[parameter]] = value


def sync_url(parameter: str) -> None:
    """Make the address bar describe the selection this page is showing.

    Setting a query parameter enqueues a URL update; it does not rerun the
    script, so this is safe to call on every run of an owning page.
    """
    value = st.session_state.get(ITEM_KEYS.get(parameter, ""))
    if value in (None, ""):
        # Nothing selected: the URL must not keep advertising one.
        if parameter in st.query_params:
            del st.query_params[parameter]
        return
    if st.query_params.get(parameter) != str(value):
        st.query_params[parameter] = str(value)


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
    """Open an item on the page that owns it.

    The selection goes into session state only. Writing the URL parameter here
    as well would add a history entry for a URL the very next navigation
    throws away (``st.switch_page`` does not carry the query string), leaving
    the browser's back button walking through states the user never saw. The
    target page writes the real URL on arrival via ``sync_url``.
    """
    st.session_state[ITEM_KEYS[parameter]] = value
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


def open_search(term: str) -> None:
    _open("q", str(term), "search")


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
