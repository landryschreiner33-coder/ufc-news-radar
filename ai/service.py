"""The AI service: one API the rest of the app calls.

Two kinds of function live here:

* **Deterministic analysis** (classify_story, detect_duplicates, group_story,
  assess_source_support, detect_rumor, calculate_relevance, identify_fighters,
  identify_event).  These are rule-based on purpose - a dashboard about
  verification must be able to explain itself, and they must work with no API
  key.  They delegate to the processors.
* **Generation** (summaries, scripts, hooks, angles, questions, reporting
  checks, social-post analysis).  These use the configured AI provider when
  one is available and fall back to the template builders otherwise.  Results
  are cached against a fingerprint of the exact source material, so identical
  material is never sent to a provider twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ai import prompts, templates
from ai.context import StoryContext, build_story_context
from ai.factory import get_provider, provider_status
from ai.grounding import check_grounding
from database import repo_ai as ai_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import StoryStatus
from processors import relevance as relevance_mod
from processors import support as support_mod
from processors import verification as verification_mod
from processors.categorize import classify_text
from processors.clustering import StoryMatcher, assign_article
from processors.entities import find_events, find_fighters, get_fighter_index
from processors.similarity import SimilarityIndex, title_similarity
from utils.logging_setup import get_logger
from utils.textutil import sha1

logger = get_logger(__name__)

TEMPLATE_NOTICE = (
    "Template mode: built directly from your collected sources (no AI key configured)."
)


@dataclass
class Generated:
    """One piece of generated text plus everything needed to trust it."""

    kind: str
    text: str = ""
    items: List[str] = field(default_factory=list)
    sections: Dict[str, List[str]] = field(default_factory=dict)
    provider: str = "template"
    model: Optional[str] = None
    from_cache: bool = False
    notice: str = ""
    warnings: List[str] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_template(self) -> bool:
        return self.provider == "template"


# ============================================================ deterministic ==
def classify_story(title: str, body: str = "") -> Dict[str, Any]:
    """Category + hedging + official/denial signals for a piece of text."""
    signals = classify_text(title, body)
    return {
        "category": signals.category,
        "keywords": signals.keywords,
        "speculation_score": signals.speculation_score,
        "has_official_language": signals.has_official_language,
        "has_denial": signals.has_denial,
        "attribution_outlets": signals.attribution_outlets,
        "scores": signals.category_scores,
        "method": "rule-based",
    }


def detect_duplicates(
    article: Dict[str, Any], candidates: List[Dict[str, Any]], threshold: float = 0.8
) -> List[Dict[str, Any]]:
    """Find near-identical articles (same URL, or very similar headline+text)."""
    from utils.textutil import url_hash

    target_hash = url_hash(article.get("url"))
    documents = [
        f"{candidate.get('title','')} {candidate.get('excerpt','')}" for candidate in candidates
    ]
    index = SimilarityIndex(documents)
    scores = index.query(f"{article.get('title','')} {article.get('excerpt','')}")
    duplicates: List[Dict[str, Any]] = []
    for position, candidate in enumerate(candidates):
        if candidate.get("id") and candidate.get("id") == article.get("id"):
            continue
        headline_similarity = title_similarity(article.get("title") or "", candidate.get("title") or "")
        content_similarity = scores[position] if position < len(scores) else 0.0
        same_url = bool(target_hash) and url_hash(candidate.get("url")) == target_hash
        if same_url or headline_similarity >= threshold or content_similarity >= threshold:
            duplicates.append({
                "article": candidate,
                "reason": "same URL" if same_url else "near-identical text",
                "headline_similarity": round(headline_similarity, 3),
                "content_similarity": round(content_similarity, 3),
            })
    return duplicates


def group_story(article: Dict[str, Any], matcher: Optional[StoryMatcher] = None) -> Dict[str, Any]:
    """Attach an article to the right story (creating one when needed)."""
    decision = assign_article(article, matcher)
    return {
        "story_id": decision.story_id,
        "created": decision.created,
        "score": round(decision.score, 3),
        "reasons": decision.reasons,
    }


def assess_source_support(story_id: int) -> Dict[str, Any]:
    """The automated source-support assessment for a story (never a probability)."""
    context = build_story_context(story_id)
    if context is None:
        return {"error": "story not found"}
    verdict = verification_mod.evaluate_story(context.story, context.articles, context.social_posts)
    assessment = support_mod.assess_support(verdict, context.articles, context.social_posts)
    return {
        "score": assessment.score,
        "label": assessment.label,
        "reasons": assessment.reasons,
        "disclaimer": assessment.disclaimer,
        "status": verdict.status,
        "status_reasons": verdict.reasons,
        "independent_sources": verdict.independent_source_count,
        "official_confirmed": verdict.official_confirmed,
        "conflicts": verdict.conflict_notes,
    }


def detect_rumor(story_id: int) -> Dict[str, Any]:
    """Is this a rumour, and what makes it one?"""
    context = build_story_context(story_id)
    if context is None:
        return {"error": "story not found"}
    verdict = verification_mod.evaluate_story(context.story, context.articles, context.social_posts)
    is_rumor = verdict.status in (
        StoryStatus.RUMOR.value, StoryStatus.UNVERIFIED.value, StoryStatus.FIGHTER_CLAIM.value
    )
    return {
        "is_rumor": is_rumor,
        "status": verdict.status,
        "speculation_score": verdict.speculation_score,
        "independent_sources": verdict.independent_source_count,
        "official_confirmed": verdict.official_confirmed,
        "reasons": verdict.reasons,
    }


def calculate_relevance(story_id: int) -> Dict[str, Any]:
    context = build_story_context(story_id)
    if context is None:
        return {"error": "story not found"}
    result = relevance_mod.score_story(context.story, context.articles, context.social_posts)
    return {
        "score": result.score,
        "band": relevance_mod.relevance_band(result.score),
        "breakdown": result.breakdown,
        "disclaimer": relevance_mod.DISCLAIMER,
    }


def identify_fighters(text: str) -> List[str]:
    return find_fighters(text, get_fighter_index())


def identify_event(text: str) -> Optional[str]:
    events = find_events(text)
    return events[0] if events else None


# ================================================================ generation ==
def _generate(
    kind: str,
    context: StoryContext,
    prompt: str,
    fallback,
    max_tokens: int = 900,
    temperature: float = 0.3,
    force_refresh: bool = False,
) -> Generated:
    """Run one generation with caching, grounding checks and template fallback."""
    provider = get_provider()
    prompt_hash = sha1(f"{kind}|{context.fingerprint()}|{provider.name}|{provider.model}")
    story_id = context.story.get("id")

    if not force_refresh:
        cached = ai_repo.get_summary(story_id, kind, prompt_hash, provider.name)
        if cached:
            return Generated(
                kind=kind,
                text=cached["content"],
                items=(cached.get("content_json") or {}).get("items", []) if isinstance(
                    cached.get("content_json"), dict) else [],
                sections=(cached.get("content_json") or {}).get("sections", {}) if isinstance(
                    cached.get("content_json"), dict) else {},
                provider=cached["provider"],
                model=cached.get("model"),
                from_cache=True,
                notice=TEMPLATE_NOTICE if cached["provider"] == "template" else
                       f"Generated with {cached['provider']} and cached.",
                warnings=(cached.get("grounding_json") or {}).get("warnings", []),
                sources=cached.get("sources_json") or context.sources_for_display(),
            )

    result = Generated(kind=kind, sources=context.sources_for_display())
    if provider.is_configured:
        response = provider.generate(prompt, system=prompts.SYSTEM_PROMPT,
                                     max_tokens=max_tokens, temperature=temperature)
        if response.ok and response.text.strip():
            result.text = response.text.strip()
            result.provider = response.provider
            result.model = response.model
            report = check_grounding(result.text, context.source_text)
            result.warnings = report.warnings
            result.notice = f"Generated with {response.provider} ({response.model})."
        else:
            logger.info("AI generation failed (%s) - using templates", response.error)
            result = _fallback_result(kind, context, fallback)
            result.notice = (
                f"AI provider unavailable ({response.error_kind or 'error'}) - "
                "showing template output built from your sources."
            )
    else:
        result = _fallback_result(kind, context, fallback)

    ai_repo.save_summary(
        story_id=story_id,
        kind=kind,
        content=result.text,
        provider=result.provider,
        prompt_hash=prompt_hash,
        model=result.model,
        content_json={"items": result.items, "sections": result.sections},
        sources=result.sources,
        grounding={"warnings": result.warnings},
    )
    return result


def _fallback_result(kind: str, context: StoryContext, fallback) -> Generated:
    value = fallback(context)
    result = Generated(kind=kind, provider="template", model="built-in templates",
                       notice=TEMPLATE_NOTICE, sources=context.sources_for_display())
    if isinstance(value, dict):
        result.sections = {key: list(values) for key, values in value.items()}
        result.text = "\n".join(
            f"{key.replace('_', ' ').upper()}\n" + "\n".join(f"- {line}" for line in values)
            for key, values in value.items()
        )
    elif isinstance(value, list):
        result.items = list(value)
        result.text = "\n".join(f"- {item}" for item in value)
    else:
        result.text = str(value)
    return result


def _context_or_none(story_id: int) -> Optional[StoryContext]:
    return build_story_context(story_id)


def summarize_story(story_id: int, force_refresh: bool = False) -> Generated:
    context = _context_or_none(story_id)
    if context is None:
        return Generated(kind="summary", text="Story not found.")
    return _generate("summary", context, prompts.summary_prompt(context),
                     templates.build_summary, max_tokens=400, force_refresh=force_refresh)


def generate_reporting_check(story_id: int, force_refresh: bool = False) -> Generated:
    context = _context_or_none(story_id)
    if context is None:
        return Generated(kind="check", text="Story not found.")
    result = _generate("check", context, prompts.reporting_check_prompt(context),
                       templates.build_reporting_check, max_tokens=1100,
                       force_refresh=force_refresh)
    if not result.sections:
        result.sections = _parse_sections(result.text)
    return result


def generate_tiktok_script(story_id: int, seconds: int = 30, force_refresh: bool = False) -> Generated:
    context = _context_or_none(story_id)
    if context is None:
        return Generated(kind=f"script_{seconds}", text="Story not found.")
    return _generate(
        f"script_{seconds}", context, prompts.script_prompt(context, seconds),
        lambda ctx: templates.build_script(ctx, seconds),
        max_tokens=700, temperature=0.5, force_refresh=force_refresh,
    )


def generate_tiktok_hooks(story_id: int, force_refresh: bool = False) -> Generated:
    context = _context_or_none(story_id)
    if context is None:
        return Generated(kind="hooks", text="Story not found.")
    result = _generate("hooks", context, prompts.hooks_prompt(context), templates.build_hooks,
                       max_tokens=400, temperature=0.6, force_refresh=force_refresh)
    if not result.items:
        result.items = _parse_lines(result.text)
    return result


def generate_story_angle(story_id: int, force_refresh: bool = False) -> Generated:
    context = _context_or_none(story_id)
    if context is None:
        return Generated(kind="angle", text="Story not found.")
    return _generate("angle", context, prompts.angle_prompt(context), templates.build_story_angle,
                     max_tokens=350, temperature=0.4, force_refresh=force_refresh)


def generate_questions(story_id: int, force_refresh: bool = False) -> Generated:
    context = _context_or_none(story_id)
    if context is None:
        return Generated(kind="questions", text="Story not found.")
    result = _generate("questions", context, prompts.questions_prompt(context),
                       templates.build_questions, max_tokens=400, force_refresh=force_refresh)
    if not result.items:
        result.items = _parse_lines(result.text)
    return result


def key_facts(story_id: int) -> List[str]:
    """Always template-built: these are facts from the database, not prose."""
    context = _context_or_none(story_id)
    return templates.build_key_facts(context) if context else []


def knowledge_breakdown(story_id: int) -> Dict[str, List[str]]:
    context = _context_or_none(story_id)
    return templates.build_knowledge_breakdown(context) if context else {}


def analyze_social_post(post_row_id: int, force_refresh: bool = False) -> Dict[str, Any]:
    """Describe one X post without inventing its context."""
    post = social_repo.get_post(post_row_id)
    if post is None:
        return {"error": "post not found"}
    related_stories: List[Dict[str, Any]] = []
    for name in (post.get("fighters") or [])[:2]:
        related_stories.extend(stories_repo.stories_for_fighter(name, limit=3))
    analysis = templates.build_social_post_analysis(post, related_stories)

    provider = get_provider()
    if not provider.is_configured:
        analysis["provider"] = "template"
        analysis["notice"] = TEMPLATE_NOTICE
        return analysis

    context_text = "\n".join(
        f"- {story.get('headline')} [{story.get('status')}]" for story in related_stories[:5]
    )
    cache_key = sha1(f"social|{post.get('post_id')}|{provider.name}|{len(related_stories)}")
    if not force_refresh:
        cached = ai_repo.cache_get(cache_key)
        if cached:
            analysis["ai_text"] = cached
            analysis["provider"] = provider.name
            analysis["notice"] = f"Generated with {provider.name} (cached)."
            return analysis
    response = provider.generate(
        prompts.social_post_prompt(context_text, post.get("text") or "",
                                   post.get("account_type") or "UNKNOWN",
                                   post.get("username") or "unknown"),
        system=prompts.SYSTEM_PROMPT, max_tokens=600,
    )
    if response.ok:
        ai_repo.cache_put(cache_key, "social", provider.name, response.text, provider.model)
        analysis["ai_text"] = response.text
        analysis["provider"] = provider.name
        analysis["notice"] = f"Generated with {provider.name} ({provider.model})."
        report = check_grounding(response.text, post.get("text") or "")
        analysis["warnings"] = report.warnings
    else:
        analysis["provider"] = "template"
        analysis["notice"] = (
            f"AI provider unavailable ({response.error_kind}) - showing template analysis."
        )
    return analysis


def ai_status() -> Dict[str, Any]:
    status = provider_status()
    status["cached_generations"] = ai_repo.cache_size()
    return status


# ------------------------------------------------------------- parsing -----
def _parse_lines(text: str) -> List[str]:
    lines = []
    for raw in (text or "").splitlines():
        cleaned = raw.strip().lstrip("-*\u2022 ").strip()
        cleaned = cleaned.lstrip("0123456789.) ").strip()
        if cleaned:
            lines.append(cleaned)
    return lines[:10]


def _parse_sections(text: str) -> Dict[str, List[str]]:
    """Parse 'HEADING' + '- bullet' output into a dict."""
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("-") or line.startswith("\u2022"):
            if current:
                sections.setdefault(current, []).append(line.lstrip("-\u2022 ").strip())
        elif line.replace(" ", "").isupper() or line.endswith(":"):
            current = line.rstrip(":").strip().lower().replace(" ", "_")
            sections.setdefault(current, [])
    return sections
