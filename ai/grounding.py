"""Check that generated text stays inside the collected source material.

Two checks, both conservative:
  * every quoted span in the output must appear in the sources;
  * fight records / money figures / precise statistics must appear too.

The result is advisory: the UI shows warnings next to generated text so
nothing unverified is read out on camera by accident.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from utils.textutil import find_quotes, normalize_text

_RECORD_RE = re.compile(r"\b\d{1,3}-\d{1,3}(?:-\d{1,3})?\b")
_MONEY_RE = re.compile(r"[$£€]\s?\d[\d,\.]*\s?(?:million|billion|k)?", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")


@dataclass
class GroundingReport:
    ok: bool = True
    warnings: List[str] = field(default_factory=list)
    unsupported_quotes: List[str] = field(default_factory=list)
    unsupported_figures: List[str] = field(default_factory=list)
    checked_quotes: int = 0
    checked_figures: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "warnings": self.warnings,
            "unsupported_quotes": self.unsupported_quotes,
            "unsupported_figures": self.unsupported_figures,
            "checked_quotes": self.checked_quotes,
            "checked_figures": self.checked_figures,
        }


def check_grounding(generated: str, source_text: str) -> GroundingReport:
    report = GroundingReport()
    if not generated:
        return report
    haystack = normalize_text(source_text or "")

    for quote in find_quotes(generated):
        report.checked_quotes += 1
        if not _appears(quote, haystack):
            report.unsupported_quotes.append(quote)

    figures: List[str] = []
    figures.extend(_RECORD_RE.findall(generated))
    figures.extend(_MONEY_RE.findall(generated))
    figures.extend(_PERCENT_RE.findall(generated))
    for figure in figures:
        report.checked_figures += 1
        if normalize_text(figure) not in haystack:
            report.unsupported_figures.append(figure.strip())

    if report.unsupported_quotes:
        report.ok = False
        report.warnings.append(
            f"{len(report.unsupported_quotes)} quoted passage(s) do not appear in the collected "
            "sources - do not read them out."
        )
    if report.unsupported_figures:
        report.ok = False
        report.warnings.append(
            "Figures not found in the collected sources: "
            + ", ".join(sorted(set(report.unsupported_figures))[:5])
        )
    return report


def _appears(quote: str, normalized_source: str) -> bool:
    """A quote counts as supported when it (or a long run of it) is present."""
    normalized_quote = normalize_text(quote)
    if not normalized_quote:
        return True
    if normalized_quote in normalized_source:
        return True
    words = normalized_quote.split()
    if len(words) >= 8:  # allow light trimming by the model
        for size in (8, 6):
            for start in range(0, max(1, len(words) - size + 1)):
                window = " ".join(words[start:start + size])
                if window and window in normalized_source:
                    return True
    return False
