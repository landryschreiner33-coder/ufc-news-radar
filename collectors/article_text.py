"""Optional main-content extraction for article pages.

Why this is deliberately limited
--------------------------------
The app stores a *short extract* for analysis and a *short excerpt* for
display - never a full copy of a publisher's article. Readers are always sent
to the original link. Extraction also costs a request per article, so the
pipeline only does it for a small number of high-value articles per run.
"""
from __future__ import annotations

from typing import Optional, Tuple

from utils.http import HttpClient, get_client
from utils.logging_setup import get_logger
from utils.textutil import collapse_whitespace, excerpt_from

logger = get_logger(__name__)

# Hard limit on how much article text is ever kept in the database.
MAX_STORED_CHARS = 1500


def extract_article_text(url: str, client: Optional[HttpClient] = None) -> Tuple[str, str, Optional[str]]:
    """Return ``(analysis_snippet, display_excerpt, error)`` for one article.

    Never raises: a failure returns empty strings plus the reason.
    """
    client = client or get_client()
    response = client.get(url, max_retries=1)
    if not response.ok:
        return "", "", response.error or f"HTTP {response.status_code}"
    return extract_from_html(response.text)


def extract_from_html(html: str) -> Tuple[str, str, Optional[str]]:
    if not html:
        return "", "", "empty document"
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html, include_comments=False, include_tables=False, favor_precision=True
        ) or ""
    except Exception as exc:  # extraction library problem must not break collection
        logger.debug("trafilatura failed: %s", exc)
        text = ""
    if not text:
        text = _fallback_extract(html)
    if not text:
        return "", "", "no main content found"
    cleaned = collapse_whitespace(text)
    return cleaned[:MAX_STORED_CHARS], excerpt_from(cleaned, 320), None


def _fallback_extract(html: str) -> str:
    """Very small BeautifulSoup fallback when trafilatura finds nothing."""
    try:
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        paragraphs = [collapse_whitespace(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
        paragraphs = [p for p in paragraphs if len(p) > 60]
        return " ".join(paragraphs[:12])
    except Exception:
        return ""
