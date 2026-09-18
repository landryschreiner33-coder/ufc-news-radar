"""The 'no AI provider' provider.

It never generates prose.  Callers fall back to the template builders in
``ai/templates.py``, which assemble text strictly from collected source
material and label it as template mode.
"""
from __future__ import annotations

from typing import Optional

from ai.provider import AIProvider, AIResponse


class NoProvider(AIProvider):
    name = "template"

    def __init__(self, model: Optional[str] = None) -> None:
        super().__init__(model or "built-in templates")

    @property
    def is_configured(self) -> bool:
        return False

    @property
    def label(self) -> str:
        return "Template mode (no AI key configured)"

    def generate(self, prompt: str, system: Optional[str] = None, max_tokens: int = 900,
                 temperature: float = 0.3) -> AIResponse:
        return AIResponse(
            provider=self.name,
            error="No AI provider configured - using built-in templates built from your sources.",
            error_kind="not_configured",
        )
