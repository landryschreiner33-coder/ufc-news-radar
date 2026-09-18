"""Article normalisation: URLs, titles, timestamps, excerpts."""
from __future__ import annotations

from collectors.article_text import MAX_STORED_CHARS, extract_from_html
from utils.textutil import (
    canonical_url, clean_title, domain_of, excerpt_from, find_quotes, normalize_title,
    slugify, truncate, url_hash,
)
from utils.timeutil import humanize_age, parse_iso, struct_time_to_iso, to_iso, utcnow_iso


def test_canonical_url_strips_tracking_and_case():
    assert canonical_url("https://WWW.Example.com/a/b/?utm_source=x&id=7#frag") == \
        "https://example.com/a/b?id=7"
    assert canonical_url("http://example.com/a/") == canonical_url("https://www.example.com/a")


def test_canonical_url_unwraps_google_news_links():
    wrapped = "https://news.google.com/rss/articles/CBM?oc=5&url=https%3A%2F%2Fespn.com%2Fmma%2F1"
    assert canonical_url(wrapped) == "https://espn.com/mma/1"


def test_canonical_url_handles_junk():
    assert canonical_url("") == ""
    assert canonical_url(None) == ""
    assert canonical_url("not a url") == "not a url"


def test_url_hash_matches_for_equivalent_urls():
    assert url_hash("https://a.test/x?utm_medium=rss") == url_hash("http://www.a.test/x")


def test_title_cleaning_removes_publisher_suffix():
    assert normalize_title("Jones vs. Aspinall official | MMA Junkie") == "jones vs aspinall official"
    assert clean_title("Jones &amp; Aspinall set - MMA Fighting") == "Jones & Aspinall set"


def test_timestamp_parsing_is_forgiving():
    assert parse_iso("Tue, 16 Sep 2026 14:03:00 -0400") is not None
    assert parse_iso("2026-09-16T10:00:00Z") is not None
    assert parse_iso("nonsense date") is None
    assert parse_iso(None) is None
    assert to_iso(parse_iso("2026-09-16T10:00:00+00:00")) == "2026-09-16T10:00:00Z"


def test_struct_time_conversion():
    import time

    assert struct_time_to_iso(time.gmtime()).endswith("Z")
    assert struct_time_to_iso(None) is None


def test_humanize_age_reads_naturally():
    assert humanize_age(utcnow_iso()) in ("just now", "0m ago")
    assert humanize_age(None) == "unknown time"


def test_excerpts_stay_short():
    long_text = "This is a sentence. " * 80
    excerpt = excerpt_from(long_text, 320)
    assert len(excerpt) <= 330
    assert truncate(long_text, 100).endswith("...")


def test_article_extraction_limits_stored_text():
    body = "<html><body><article>" + "<p>" + ("Long paragraph of article text. " * 200) + "</p>" + \
           "</article></body></html>"
    snippet, excerpt, error = extract_from_html(body)
    assert error is None
    assert 0 < len(snippet) <= MAX_STORED_CHARS, "never store a whole article"
    assert len(excerpt) <= 330


def test_article_extraction_reports_failure():
    snippet, excerpt, error = extract_from_html("")
    assert snippet == "" and error is not None


def test_find_quotes_and_slugify():
    assert find_quotes('He said "I am ready for anyone in the division" after.') == \
        ["I am ready for anyone in the division"]
    assert slugify("Jon Jones vs. Tom Aspinall!") == "jon-jones-vs-tom-aspinall"
    assert domain_of("https://www.mmafighting.com/x") == "mmafighting.com"
