"""Anthropic Claude provider (Messages API over plain HTTPS).

The API key is read from the environment and is never logged or returned.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ai.provider import AIProvider, AIResponse
from utils.config import get_config
from utils.http import HttpClient, get_client
from utils.logging_setup import get_logger

logger = get_logger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, model: Optional[str] = None, client: Optional[HttpClient] = None) -> None:
        config = get_config().ai
        super().__init__(model or config.anthropic_model)
        self._api_key = config.anthropic_api_key
        self.http = client or get_client()

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 900,
        temperature: float = 0.3,
    ) -> AIResponse:
        if not self.is_configured:
            return AIResponse(provider=self.name, error="ANTHROPIC_API_KEY is not set",
                              error_kind="not_configured")
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        response = self.http.post_json(
            API_URL,
            payload,
            headers={"x-api-key": self._api_key, "anthropic-version": API_VERSION},
        )
        if not response.ok:
            kind = "auth" if response.status_code in (401, 403) else (response.error_kind or "http")
            message = response.error or f"HTTP {response.status_code}"
            logger.warning("Anthropic request failed: %s", message[:200])
            return AIResponse(provider=self.name, model=self.model, error=message, error_kind=kind)
        payload_out = response.json() or {}
        blocks = payload_out.get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if not text:
            return AIResponse(provider=self.name, model=self.model,
                              error="empty response from provider", error_kind="parse")
        usage = payload_out.get("usage") or {}
        return AIResponse(
            ok=True, text=text, provider=self.name, model=self.model,
            tokens_in=usage.get("input_tokens"), tokens_out=usage.get("output_tokens"),
        )
