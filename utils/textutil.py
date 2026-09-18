"""Text + URL normalisation helpers shared by collectors and processors."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from typing import Iterable, List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])")

# Tracking parameters that change per visit but not per article.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_name", "utm_id", "utm_social", "utm_social-type", "fbclid", "gclid",
    "igshid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "cmpid",
    "smid", "partner", "sr_share", "amp", "outputType", "s", "src", "spm",
    "__twitter_impression", "guccounter", "guce_referrer", "guce_referrer_sig",
}

# Words removed before similarity comparison. Deliberately small: MMA copy is
# short, so dropping too much destroys the signal.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "at", "by", "for",
    "with", "about", "into", "to", "from", "in", "on", "off", "out", "over",
    "under", "again", "then", "once", "here", "there", "all", "any", "both",
    "each", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "can", "will",
    "just", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "it", "its", "this", "that", "these",
    "those", "he", "she", "they", "them", "his", "her", "their", "as",
    "after", "before", "says", "said", "say", "new", "report", "reports",
}

# Publisher names that trail headlines, e.g. "... - MMA Fighting".
_TITLE_SUFFIX_RE = re.compile(
    r"\s*[\|–—\-]\s*("
    r"mma fighting|mmafighting(\.com)?|mma junkie|mmajunkie(\.com)?|sherdog(\.com)?|"
    r"espn(\.com)?|ufc(\.com)?|bloody elbow|mmamania(\.com)?|mma mania|"
    r"bjpenn(\.com)?|lowkick ?mma|the mac life|cageside press|yahoo sports|"
    r"sports illustrated|fox sports|cbs sports|the athletic|usa today"
    r")\s*$",
    re.IGNORECASE,
)


def strip_html(value: Optional[str]) -> str:
    """Remove tags/entities from feed summaries. Never raises."""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", str(value))
    text = html.unescape(text)
    return collapse_whitespace(text)


def collapse_whitespace(value: Optional[str]) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", str(value)).strip()


def strip_accents(value: str) -> str:
    """'Jose Aldo' == 'José Aldo' for matching purposes."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(value: Optional[str]) -> str:
    """Lowercase, de-accent, drop punctuation - used for fuzzy comparison."""
    if not value:
        return ""
    text = strip_accents(str(value)).lower()
    text = text.replace("&", " and ")
    text = _PUNCT_RE.sub(" ", text)
    return collapse_whitespace(text)


def normalize_title(title: Optional[str]) -> str:
    """Normalised headline with the publisher suffix removed."""
    if not title:
        return ""
    cleaned = strip_html(title)
    previous = None
    while previous != cleaned:  # e.g. "Headline - MMA Fighting - Yahoo"
        previous = cleaned
        cleaned = _TITLE_SUFFIX_RE.sub("", cleaned).strip()
    return normalize_text(cleaned)


def clean_title(title: Optional[str]) -> str:
    """Display headline: entities decoded, publisher suffix removed."""
    if not title:
        return ""
    cleaned = strip_html(title)
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _TITLE_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned


def tokens(value: Optional[str], drop_stopwords: bool = True) -> List[str]:
    words = normalize_text(value).split()
    if drop_stopwords:
        words = [w for w in words if w not in _STOPWORDS and len(w) > 1]
    return words


def token_set(value: Optional[str], drop_stopwords: bool = True) -> set:
    return set(tokens(value, drop_stopwords))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap_ratio(left: Iterable[str], right: Iterable[str]) -> float:
    """Shared tokens divided by the size of the *smaller* set."""
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def slugify(value: str, max_length: int = 80) -> str:
    slug = normalize_text(value).replace(" ", "-")
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:max_length] or "story"


def sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()


def canonical_url(url: Optional[str]) -> str:
    """Normalise a URL so the same article from two feeds hashes identically.

    Drops tracking parameters, fragments, default ports, 'www.' and trailing
    slashes.  Google News wrapper links are unwrapped when the original URL is
    present in the query string.
    """
    if not url:
        return ""
    raw = str(url).strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    if parsed.netloc.endswith("news.google.com"):
        query = parse_qs(parsed.query)
        for key in ("url", "u"):
            if query.get(key):
                return canonical_url(query[key][0])
    scheme = (parsed.scheme or "https").lower()
    if scheme not in ("http", "https"):
        return raw
    if not parsed.netloc:
        return raw  # not a URL at all - hand it back untouched
    # http:// and https:// copies of one article are the same article.
    scheme = "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.endswith(":80") or netloc.endswith(":443"):
        netloc = netloc.rsplit(":", 1)[0]
    query_pairs = [
        (key, value)
        for key, value in parse_qs(parsed.query, keep_blank_values=False).items()
        if key.lower() not in _TRACKING_PARAMS
    ]
    flat_query = "&".join(
        f"{key}={value[0]}" for key, value in sorted(query_pairs) if value
    )
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", flat_query, ""))


def url_hash(url: Optional[str]) -> str:
    return sha1(canonical_url(url))


def domain_of(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        netloc = urlparse(str(url)).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0]


def sentences(text: Optional[str]) -> List[str]:
    if not text:
        return []
    cleaned = collapse_whitespace(strip_html(text))
    if not cleaned:
        return []
    return [s.strip() for s in _SENTENCE_RE.split(cleaned) if s.strip()]


def truncate(text: Optional[str], max_chars: int = 280, suffix: str = "...") -> str:
    """Shorten to whole words. Used to keep excerpts short (see COPYRIGHT)."""
    cleaned = collapse_whitespace(strip_html(text))
    if not cleaned or len(cleaned) <= max_chars:
        return cleaned
    clipped = cleaned[:max_chars]
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")]
    return clipped.rstrip(" ,.;:-") + suffix


def excerpt_from(text: Optional[str], max_chars: int = 320) -> str:
    """A short, attributed excerpt - never the whole article."""
    parts = sentences(text)
    if not parts:
        return ""
    out: List[str] = []
    total = 0
    for sentence in parts:
        if total + len(sentence) > max_chars and out:
            break
        out.append(sentence)
        total += len(sentence) + 1
        if total >= max_chars:
            break
    return truncate(" ".join(out), max_chars)


def contains_any(text: Optional[str], needles: Iterable[str]) -> bool:
    haystack = normalize_text(text)
    return any(normalize_text(n) and normalize_text(n) in haystack for n in needles)


def find_quotes(text: Optional[str], min_words: int = 4) -> List[str]:
    """Extract double-quoted spans - used by the AI grounding checker."""
    if not text:
        return []
    found = re.findall(r"[\"“]([^\"”]{10,400})[\"”]", str(text))
    return [q.strip() for q in found if len(q.split()) >= min_words]
