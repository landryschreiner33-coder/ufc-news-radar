"""Pick the AI provider named in the environment."""
from __future__ import annotations

from typing import Dict, Optional

from ai.provider import AIProvider
from ai.providers.anthropic_provider import AnthropicProvider
from ai.providers.none_provider import NoProvider
from ai.providers.openai_provider import OpenAIProvider
from utils.config import get_config

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "none": NoProvider,
    "template": NoProvider,
}


def available_providers() -> list:
    return ["none", "anthropic", "openai"]


def get_provider(name: Optional[str] = None, refresh: bool = False) -> AIProvider:
    config = get_config(refresh=refresh)
    key = (name or config.ai.provider or "none").strip().lower()
    provider_class = PROVIDERS.get(key, NoProvider)
    provider = provider_class()
    if not provider.is_configured and key not in ("none", "template"):
        # Selected but unusable: fall back to templates rather than failing.
        return NoProvider()
    return provider


def provider_status() -> Dict[str, object]:
    config = get_config(refresh=True)
    provider = get_provider()
    return {
        "selected": config.ai.provider,
        "active": provider.name,
        "label": provider.label,
        "configured": provider.is_configured,
        "note": config.ai.status_note,
        "model": provider.model,
    }
