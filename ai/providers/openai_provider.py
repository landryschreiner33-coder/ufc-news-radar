"""OpenAI (or any OpenAI-compatible) provider via the chat completions API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ai.provider import AIProvider, AIResponse
from utils.config import get_config
from utils.http import HttpClient, get_client
from utils.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, model: Optional[str] = None, client: Optional[HttpClient] = None) -> None:
        config = get_config().ai
        super().__init__(model or config.openai_model)
        self._api_key = config.openai_api_key
        self.base_url = (config.openai_base_url or DEFAULT_BASE_URL).rstrip("/")
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
            return AIResponse(provider=self.name, error="OPENAI_API_KEY is not set",
                              error_kind="not_configured")
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        response = self.http.post_json(
            f"{self.base_url}/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if not response.ok:
            kind = "auth" if response.status_code in (401, 403) else (response.error_kind or "http")
            message = response.error or f"HTTP {response.status_code}"
            logger.warning("OpenAI request failed: %s", message[:200])
            return AIResponse(provider=self.name, model=self.model, error=message, error_kind=kind)
        payload_out = response.json() or {}
        choices = payload_out.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            return AIResponse(provider=self.name, model=self.model,
                              error="empty response from provider", error_kind="parse")
        usage = payload_out.get("usage") or {}
        return AIResponse(
            ok=True, text=text, provider=self.name, model=self.model,
            tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
        )
