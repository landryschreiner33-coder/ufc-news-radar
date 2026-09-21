"""Application configuration.

Secrets come from the environment ONLY - never the database, the UI or git.
Two environments are supported and the lookup order is the same in both:

    1. a real environment variable   (works everywhere)
    2. ``.env``                      (local development)
    3. ``st.secrets``                (Streamlit Community Cloud)

Streamlit Cloud has no ``.env`` file, so secrets are pasted into the app's
Secrets box instead; reading ``st.secrets`` here is what makes the deployed
app work without changing any calling code. The reverse is also true: nothing
breaks locally when Streamlit is not running, because the lookup is wrapped
and falls through silently.

User *preferences* (thresholds, enabled sources, watchlists) live in the
``settings`` table so they can be edited from the UI - see
``database.repositories.settings_repo``.

Nothing in this module ever prints or logs a key value; helpers only report
whether a key is present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from dotenv import load_dotenv

from utils.paths import PROJECT_ROOT, resolve

_ENV_LOADED = False


def load_env(force: bool = False) -> None:
    """Load the .env file once per process."""
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return
    # Missing .env is the normal case in the cloud, and load_dotenv treats it
    # as a no-op, so there is nothing to handle here.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    _ENV_LOADED = True


_STREAMLIT: Any = None
_STREAMLIT_MISSING = False


def _streamlit() -> Any:
    """The streamlit module, or None when it is not installed.

    Imported once: a failed import is not cached by Python, and these lookups
    happen often enough that re-scanning sys.path for a missing module every
    time would be a silly thing to pay for.
    """
    global _STREAMLIT, _STREAMLIT_MISSING
    if _STREAMLIT is not None or _STREAMLIT_MISSING:
        return _STREAMLIT
    try:
        import streamlit
    except Exception:
        _STREAMLIT_MISSING = True
        return None
    _STREAMLIT = streamlit
    return streamlit


def _from_streamlit_secrets(name: str) -> Optional[str]:
    """Read one key from ``st.secrets`` if we are running under Streamlit.

    Deliberately defensive: there may be no streamlit at all, and touching
    ``st.secrets`` with no secrets file raises - configuration must never be
    the thing that crashes the app or the test suite.
    """
    st = _streamlit()
    if st is None:
        return None
    try:
        value = st.secrets.get(name)  # type: ignore[union-attr]
        if value is None:
            # Streamlit also supports [section] grouping; check one level down.
            for section in ("ufc_news_radar", "general", "secrets"):
                group = st.secrets.get(section)  # type: ignore[union-attr]
                # Streamlit hands back its own AttrDict, which is a Mapping
                # but not a dict - testing for dict quietly skipped every
                # grouped secret.
                if isinstance(group, Mapping) and name in group:
                    value = group[name]
                    break
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    except Exception:
        return None


def secrets_section(name: str) -> Dict[str, Any]:
    """One ``[section]`` table from ``st.secrets``, as a plain dict.

    Streamlit copies *top-level* string secrets into the environment, so those
    arrive through :func:`secret` like any other variable. A section does not:
    it can only be read from ``st.secrets`` itself, which is what makes this
    function necessary for ``[database]`` (see ``database/db_config.py``).

    Returns an empty dict whenever there is nothing to read - no Streamlit, no
    secrets file, no such section - because configuration must never be the
    thing that crashes the app or the test suite.

    Read fresh every time. Streamlit caches the parsed file itself and reloads
    it when it changes, so this is a dictionary lookup, and caching it here
    would only mean showing the user an error about a secret they have already
    corrected.
    """
    st = _streamlit()
    if st is None:
        return {}
    try:
        value = st.secrets.get(name)  # type: ignore[union-attr]
    except Exception:
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _env(name: str, default: str = "") -> str:
    """Environment variable, then .env, then Streamlit Cloud secrets."""
    load_env()
    value = os.getenv(name)
    if value is None or value == "":
        value = _from_streamlit_secrets(name)
    return default if value is None or value == "" else str(value).strip()


def secret(name: str, default: str = "") -> str:
    """One configuration value: environment, then .env, then st.secrets.

    The public name for the lookup this module has always done internally -
    other modules need it without reaching for a private helper.
    """
    return _env(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AIConfig:
    provider: str = "none"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    max_context_chars: int = 12000

    @property
    def is_configured(self) -> bool:
        """True only when the selected provider actually has a usable key."""
        if self.provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.provider == "openai":
            return bool(self.openai_api_key)
        return False

    @property
    def status_note(self) -> str:
        if self.provider == "none":
            return "AI provider set to 'none' - template mode is in use."
        if not self.is_configured:
            return f"AI provider '{self.provider}' selected but no API key found - template mode is in use."
        return f"AI provider '{self.provider}' is configured."


@dataclass(frozen=True)
class XConfig:
    bearer_token: str = ""
    max_searches_per_run: int = 3
    max_timelines_per_run: int = 5
    max_results_per_query: int = 25
    query_cache_minutes: int = 30

    @property
    def is_configured(self) -> bool:
        return bool(self.bearer_token)


@dataclass(frozen=True)
class AppConfig:
    db_path: str = "data/ufc_news_radar.db"
    http_timeout: int = 20
    user_agent: str = "UFCNewsRadar/1.0 (personal news dashboard)"
    debug: bool = False
    ai: AIConfig = field(default_factory=AIConfig)
    x: XConfig = field(default_factory=XConfig)

    @property
    def db_file(self) -> str:
        return str(resolve(self.db_path))


def load_config() -> AppConfig:
    """Read configuration from the environment (fresh each call)."""
    load_env()
    ai = AIConfig(
        provider=_env("AI_PROVIDER", "none").lower() or "none",
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=_env("OPENAI_BASE_URL"),
        max_context_chars=_env_int("AI_MAX_CONTEXT_CHARS", 12000),
    )
    x = XConfig(
        bearer_token=_env("X_BEARER_TOKEN"),
        max_searches_per_run=_env_int("X_MAX_SEARCHES_PER_RUN", 3),
        max_timelines_per_run=_env_int("X_MAX_TIMELINES_PER_RUN", 5),
        max_results_per_query=_env_int("X_MAX_RESULTS_PER_QUERY", 25),
        query_cache_minutes=_env_int("X_QUERY_CACHE_MINUTES", 30),
    )
    return AppConfig(
        db_path=_env("UFC_RADAR_DB", "data/ufc_news_radar.db"),
        http_timeout=_env_int("HTTP_TIMEOUT_SECONDS", 20),
        user_agent=_env("HTTP_USER_AGENT", "UFCNewsRadar/1.0 (personal news dashboard)"),
        debug=_env("UFC_RADAR_DEBUG", "0") == "1",
        ai=ai,
        x=x,
    )


_CACHED: Optional[AppConfig] = None


def get_config(refresh: bool = False) -> AppConfig:
    """Process-wide config singleton (call with refresh=True after edits)."""
    global _CACHED
    if _CACHED is None or refresh:
        _CACHED = load_config()
    return _CACHED
