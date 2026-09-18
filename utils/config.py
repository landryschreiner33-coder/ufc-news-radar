"""Application configuration.

Secrets come from environment variables / the .env file ONLY.  User
preferences (refresh interval, thresholds, watchlists...) live in the
``settings`` table so they can be edited from the UI - see
``database.repositories.settings_repo``.

Nothing in this module ever prints or logs a key value; helpers only report
whether a key is present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

from utils.paths import PROJECT_ROOT, resolve

_ENV_LOADED = False


def load_env(force: bool = False) -> None:
    """Load the .env file once per process."""
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    _ENV_LOADED = True


def _env(name: str, default: str = "") -> str:
    load_env()
    value = os.getenv(name)
    return default if value is None or value == "" else value.strip()


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
