"""AI provider interface.

The application never talks to a vendor SDK directly: it talks to this
interface.  Swapping providers is a configuration change (AI_PROVIDER in
.env), and running with no provider at all is a supported mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AIResponse:
    """Result of one generation request."""

    ok: bool = False
    text: str = ""
    provider: str = "template"
    model: Optional[str] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None      # not_configured|auth|rate_limit|http|network|parse
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    from_cache: bool = False

    @property
    def not_configured(self) -> bool:
        return self.error_kind == "not_configured"


class AIProvider:
    """Base class for AI providers."""

    name = "base"

    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model

    @property
    def is_configured(self) -> bool:
        return False

    @property
    def label(self) -> str:
        if not self.is_configured:
            return f"{self.name} (not configured)"
        return f"{self.name} ({self.model})"

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 900,
        temperature: float = 0.3,
    ) -> AIResponse:
        raise NotImplementedError

    def status(self) -> Dict[str, Any]:
        return {"provider": self.name, "model": self.model, "configured": self.is_configured}
