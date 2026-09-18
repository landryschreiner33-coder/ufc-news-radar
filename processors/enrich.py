"""Turn a freshly collected item into a fully described article/post.

Adds: source classification, fighters, events, category, keywords, hedging
score, attribution (is this a copy of someone else's reporting?) and whether
the item is an official announcement.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database import repo_settings as settings_repo
from models.types import SourceType, normalize_source_type, reliability_for
from processors.categorize import classify_text
from processors.entities import find_events, find_fighters, find_weight_classes, get_fighter_index
from utils.textutil import clean_title, domain_of, truncate


def enrich_article(item: Dict[str, Any], index: Optional[Any] = None) -> Dict[str, Any]:
    """Enrich one normalised collector item in place (and return it)."""
    index = index or get_fighter_index()
    item = dict(item)
    item["title"] = clean_title(item.get("title") or "")
    text_for_analysis = " ".join(
        str(part) for part in (item.get("title"), item.get("excerpt"), item.get("content_snippet"))
        if part
    )

    _apply_source_classification(item)

    signals = classify_text(
        item.get("title") or "",
        f"{item.get('excerpt') or ''} {item.get('content_snippet') or ''}",
        self_outlet=item.get("source_name") or item.get("domain"),
    )
    item["category"] = signals.category
    item["speculation_score"] = signals.speculation_score
    item["attribution_outlets"] = signals.attribution_outlets
    item["is_derivative"] = signals.is_derivative
    item["has_denial"] = signals.has_denial
    keywords = list(dict.fromkeys(list(item.get("keywords") or []) + signals.keywords))
    item["keywords"] = keywords[:15]

    item["fighters"] = find_fighters(text_for_analysis, index)
    known_events = [event for event in (item.get("events") or []) if event]
    item["events"] = find_events(text_for_analysis, known_events)
    item["weight_classes"] = find_weight_classes(text_for_analysis)

    source_type = normalize_source_type(item.get("source_type"))
    item["is_official"] = bool(
        source_type == SourceType.OFFICIAL.value and not signals.is_derivative
    )
    if item.get("excerpt"):
        item["excerpt"] = truncate(item["excerpt"], 320)
    return item


def _apply_source_classification(item: Dict[str, Any]) -> None:
    """Fill in source type / reliability / independence group.

    Order of precedence: the byline's own classification (a known journalist),
    then the publisher domain, then whatever the source registry said.
    """
    domain = item.get("domain") or domain_of(item.get("url"))
    item["domain"] = domain

    domain_classification = settings_repo.classify_domain(domain)
    if domain_classification:
        item.setdefault("source_type", domain_classification["classification"])
        if not item.get("reliability_weight"):
            item["reliability_weight"] = domain_classification["reliability_weight"]
        if not item.get("independence_group"):
            item["independence_group"] = domain_classification["independence_group"] or domain

    author_classification = settings_repo.classify_author(item.get("author"))
    if author_classification:
        current_weight = float(item.get("reliability_weight") or 0)
        author_weight = float(author_classification["reliability_weight"] or 0)
        if author_weight > current_weight:
            item["reliability_weight"] = author_weight
            # Keep OFFICIAL/MAJOR_NEWS labels; only upgrade unknown sources.
            if normalize_source_type(item.get("source_type")) in (
                SourceType.UNKNOWN.value, SourceType.FAN_ACCOUNT.value,
            ):
                item["source_type"] = author_classification["classification"]

    item["source_type"] = normalize_source_type(item.get("source_type"))
    if not item.get("reliability_weight"):
        item["reliability_weight"] = reliability_for(item["source_type"])
    if not item.get("independence_group"):
        item["independence_group"] = domain or item.get("source_key") or "unknown"


def enrich_social_post(post: Dict[str, Any], index: Optional[Any] = None) -> Dict[str, Any]:
    """Classify an X post: who posted it, what it is about, how hedged it is."""
    index = index or get_fighter_index()
    post = dict(post)
    text = post.get("text") or ""
    account_type = post.get("account_type") or settings_repo.classify_x_account(post.get("username"))
    post["account_type"] = normalize_source_type(account_type)
    signals = classify_text(text, "", self_outlet=post.get("username"))
    post["category"] = signals.category
    post["speculation_score"] = signals.speculation_score
    post["fighters"] = find_fighters(text, index)
    post["events"] = find_events(text)
    post["attribution_outlets"] = signals.attribution_outlets
    post["has_denial"] = signals.has_denial
    post["reliability_weight"] = reliability_for(post["account_type"])
    return post


