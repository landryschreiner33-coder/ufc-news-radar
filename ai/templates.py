"""Template mode: everything the app generates when no AI key is configured.

These builders assemble text strictly from collected material - source
headlines, source names, timestamps and the values the processors derived.
They never introduce a fact that is not in the sources, and the UI labels
their output as template mode.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ai.context import SourceRef, StoryContext
from models.types import Category, StoryStatus, category_label, status_badge, status_style
from utils.textutil import collapse_whitespace, normalize_text, sentences, truncate
from utils.timeutil import format_display, humanize_age

_PREFIX_RE = re.compile(
    r"^(report|reports|rumor|rumour|breaking|exclusive|update|opinion|video|watch)\s*[:\-]\s*",
    re.IGNORECASE,
)
_SUFFIX_RE = re.compile(
    r"\s*[,\-\u2013]\s*(per (a )?report(s)?|per sources|according to (a )?report(s)?|report says|"
    r"sources say)\.?$",
    re.IGNORECASE,
)


def clean_claim(headline: str) -> str:
    """Headline with 'Report:'-style prefixes removed, for use in prose."""
    cleaned = _PREFIX_RE.sub("", collapse_whitespace(headline or "")).strip()
    cleaned = _SUFFIX_RE.sub("", cleaned).strip()
    return cleaned[0].upper() + cleaned[1:] if cleaned else ""


def spoken_claim(headline: str) -> str:
    """Claim text that reads naturally when spoken aloud."""
    claim = clean_claim(headline)
    match = re.match(r"^(UFC\s+[\w\s\.]{1,24}?):\s*(.+)$", claim)
    if match:
        return f"At {match.group(1)}, {match.group(2).strip()}"
    return claim


def subject_of(context: StoryContext) -> str:
    fighters = context.fighters
    if len(fighters) >= 2:
        return f"{fighters[0]} and {fighters[1]}"
    if fighters:
        return fighters[0]
    if context.events:
        return context.events[0]
    return "this story"


# ------------------------------------------------------------- summaries ----
def build_summary(context: StoryContext) -> str:
    claim = clean_claim(context.headline)
    status = context.status
    source_count = context.story.get("independent_source_count") or 0
    parts: List[str] = []

    if status == StoryStatus.CONFIRMED.value and context.official_sources:
        names = ", ".join(sorted({source.name for source in context.official_sources})[:2])
        parts.append(f"{claim}. This is confirmed in official material from {names}.")
    elif status == StoryStatus.REPORTED.value:
        parts.append(
            f"{claim}. Reported by {source_count} independent credible source(s); "
            "no official confirmation is in the collected sources."
        )
    elif status == StoryStatus.DEVELOPING.value:
        parts.append(f"{claim}. The story is still moving and details may change.")
    elif status == StoryStatus.FIGHTER_CLAIM.value:
        parts.append(f"{claim}. This comes from the fighter's or team's own statement, not from the UFC.")
    elif status == StoryStatus.RUMOR.value:
        parts.append(f"{claim}. Treat this as a rumour: the collected sources do not back it up.")
    else:
        parts.append(f"{claim}. There is not enough source evidence collected yet to say more.")

    if context.conflict_notes:
        parts.append(context.conflict_notes[0])
    if context.events:
        parts.append(f"Event mentioned in the reporting: {context.events[0]}.")
    return " ".join(parts)


def build_knowledge_breakdown(context: StoryContext) -> Dict[str, List[str]]:
    """WHAT WE KNOW / IS CLAIMED / IS CONFIRMED / IS NOT CONFIRMED / CONFLICTS."""
    confirmed: List[str] = []
    claimed: List[str] = []
    known: List[str] = []
    not_confirmed: List[str] = []
    conflicting: List[str] = list(context.conflict_notes)

    for source in context.official_sources:
        confirmed.append(f"{clean_claim(source.title)} - stated by {source.name} [{source.index}]")
    for source in context.credible_sources:
        if source.is_official:
            continue
        claimed.append(f"{clean_claim(source.title)} - reported by {source.name} [{source.index}]")
    for source in context.first_person_sources:
        claimed.append(
            f"{truncate(source.excerpt or source.title, 160)} - said by {source.name} [{source.index}]"
        )

    known.append(
        f"{len(context.articles)} article(s) and {len(context.social_posts)} X post(s) have been "
        f"collected about this, first seen {humanize_age(context.story.get('first_seen_at'))}."
    )
    independent = context.story.get("independent_source_count") or 0
    known.append(
        f"{independent} independent credible source(s) are behind it "
        f"(copies of the same report are not counted)."
    )
    if context.events:
        known.append(f"The reporting ties this to {', '.join(context.events)}.")
    if context.fighters:
        known.append(f"Fighters named in the reporting: {', '.join(context.fighters)}.")

    if not context.official_sources:
        not_confirmed.append("No official UFC source in the collected material has confirmed this.")
    if independent < 2 and not context.official_sources:
        not_confirmed.append("Only one credible source so far - it has not been corroborated.")
    derivative = [source for source in context.sources if source.is_derivative]
    if derivative:
        names = ", ".join(sorted({source.name for source in derivative})[:3])
        not_confirmed.append(
            f"{len(derivative)} report(s) ({names}) credit another outlet rather than reporting "
            "independently."
        )
    for gap in missing_details(context):
        not_confirmed.append(gap)

    return {
        "what_we_know": known,
        "what_is_claimed": claimed[:8],
        "what_is_confirmed": confirmed[:8] or ["Nothing here is confirmed by an official source yet."],
        "what_is_not_confirmed": not_confirmed[:8],
        "conflicting": conflicting,
        "why_it_matters": [build_why_it_matters(context)],
    }


def missing_details(context: StoryContext) -> List[str]:
    """Which basic details the collected text does not answer."""
    haystack = normalize_text(context.source_text + " " + context.headline)
    gaps: List[str] = []
    category = context.story.get("category")

    if not context.events:
        gaps.append("No event has been named in the collected sources.")
    if category in (Category.FIGHT_ANNOUNCEMENT.value, Category.REPLACEMENT.value):
        if not re.search(r"\b(january|february|march|april|may|june|july|august|september|october|"
                         r"november|december|\d{1,2}/\d{1,2})\b", haystack):
            gaps.append("No date for the bout appears in the collected sources.")
        if not any(term in haystack for term in (
            "weight", "heavyweight", "lightweight", "welterweight", "middleweight",
            "featherweight", "bantamweight", "flyweight", "strawweight",
        )):
            gaps.append("No weight class appears in the collected sources.")
    if category == Category.INJURY.value:
        if not any(term in haystack for term in ("out of", "withdraw", "replacement", "cancel")):
            gaps.append("The sources do not say whether a bout is actually off.")
        if "timeline" not in haystack and "weeks" not in haystack and "months" not in haystack:
            gaps.append("No recovery timeline appears in the collected sources.")
    if category in (Category.CANCELLATION.value, Category.REPLACEMENT.value):
        if "replac" not in haystack:
            gaps.append("No replacement has been named in the collected sources.")
    if not any(term in haystack for term in ("said", "told", "statement", "announced", "posted")):
        gaps.append("No direct statement from those involved appears in the collected sources.")
    return gaps[:5]


def build_why_it_matters(context: StoryContext) -> str:
    category = context.story.get("category")
    subject = subject_of(context)
    event = context.events[0] if context.events else "the card"
    mapping = {
        Category.FIGHT_ANNOUNCEMENT.value:
            f"A booking involving {subject} sets the direction of {event} and the division around it.",
        Category.CANCELLATION.value:
            f"A cancellation involving {subject} changes {event} and usually starts a replacement search.",
        Category.REPLACEMENT.value:
            f"A replacement changes the matchup for {subject} and the shape of {event}.",
        Category.INJURY.value:
            f"An injury to {subject} affects their next booking and everything below them in the division.",
        Category.RETIREMENT.value: f"A retirement decision by {subject} closes out a division spot.",
        Category.SUSPENSION.value: f"A suspension takes {subject} out of the picture for a period.",
        Category.RANKING.value: f"Ranking movement changes who is next in line around {subject}.",
        Category.RESULT.value: f"The result changes what comes next for {subject}.",
        Category.TITLE.value: f"Championship status decides the whole division around {subject}.",
        Category.BUSINESS.value: "Business decisions shape the schedule, the broadcast and fighter pay.",
        Category.CONTROVERSY.value: f"It affects how {subject} is perceived and can carry consequences.",
    }
    return mapping.get(
        category,
        f"It is the newest development involving {subject} in the collected sources.",
    )


# ------------------------------------------------- check before reporting ---
def build_reporting_check(context: StoryContext) -> Dict[str, List[str]]:
    breakdown = build_knowledge_breakdown(context)
    confirmed = [
        f"✅ {item}" for item in breakdown["what_is_confirmed"]
        if not item.startswith("Nothing here is confirmed")
    ]
    reported = [f"⚠️ Reported only: {item}" for item in breakdown["what_is_claimed"][:5]]
    unconfirmed = [f"❌ {item}" for item in breakdown["what_is_not_confirmed"][:6]]
    conflicting = [f"⚔️ {item}" for item in breakdown["conflicting"]]

    mistakes: List[str] = []
    status = context.status
    if status != StoryStatus.CONFIRMED.value:
        mistakes.append(
            "Do not say \"official\" or \"confirmed\" - the collected sources do not show official "
            "confirmation."
        )
    if status in (StoryStatus.RUMOR.value, StoryStatus.UNVERIFIED.value):
        mistakes.append("Say \"rumour\" or \"unconfirmed report\", and name who is claiming it.")
    if status == StoryStatus.FIGHTER_CLAIM.value:
        mistakes.append(
            "This is the fighter's/team's own claim - attribute it to them, not to the UFC."
        )
    if context.conflict_notes:
        mistakes.append("Sources disagree here - say so on camera instead of picking one side.")
    derivative = [source for source in context.sources if source.is_derivative]
    if derivative:
        mistakes.append(
            "Several outlets are repeating one original report - do not present that as multiple "
            "independent confirmations."
        )
    if (context.story.get("independent_source_count") or 0) < 2 and not context.official_sources:
        mistakes.append("Only one source so far - avoid \"everyone is reporting\" framing.")
    mistakes.append("Never read out a quote you have not seen in one of the linked sources.")

    return {
        "confirmed_facts": confirmed or ["Nothing in the collected sources is officially confirmed."],
        "reported_claims": reported or ["No credible-outlet reporting has been collected yet."],
        "unconfirmed": unconfirmed or ["No obvious gaps detected."],
        "conflicting": conflicting or ["No contradictions detected in the collected sources."],
        "missing": [f"❓ {item}" for item in missing_details(context)] or
                   ["❓ Nothing obvious missing from the collected material."],
        "context": [build_why_it_matters(context)],
        "potential_mistakes": mistakes,
    }


# -------------------------------------------------------- TikTok builders ---
def build_key_facts(context: StoryContext) -> List[str]:
    facts: List[str] = []
    status = status_style(context.status)
    facts.append(f"Status: {status.emoji} {status.label} - {status.meaning}")
    if context.fighters:
        facts.append("Fighters: " + ", ".join(context.fighters[:4]))
    if context.events:
        facts.append("Event: " + ", ".join(context.events[:2]))
    facts.append(f"Category: {category_label(context.story.get('category'))}")
    facts.append(
        f"Sources: {len(context.articles)} article(s), "
        f"{context.story.get('independent_source_count') or 0} independent"
    )
    if context.official_sources:
        facts.append("Official material from: " + ", ".join(
            sorted({source.name for source in context.official_sources})[:2]))
    facts.append(f"First reported: {format_display(context.story.get('first_seen_at'))}")
    facts.append(f"Last update: {humanize_age(context.story.get('last_updated_at'))}")
    if context.conflict_notes:
        facts.append("Conflict: " + context.conflict_notes[0])
    return facts


def build_hooks(context: StoryContext) -> List[str]:
    """Openers that grab attention without overstating what is known."""
    subject = subject_of(context)
    claim = spoken_claim(context.headline)
    short_claim = truncate(claim, 90)
    status = context.status
    independent = context.story.get("independent_source_count") or 0
    hooks: List[str] = []

    if status == StoryStatus.CONFIRMED.value:
        hooks += [
            f"It's official: {short_claim}.",
            f"The UFC just made it official - {subject}.",
            f"This one is confirmed, and it changes things for {subject}.",
        ]
    elif status == StoryStatus.REPORTED.value:
        hooks += [
            f"{independent} outlets are reporting this, and the UFC has not confirmed it yet.",
            f"Here's what's being reported about {subject} - and what still isn't official.",
            f"Big if true: {short_claim}. Here's who is actually reporting it.",
        ]
    elif status == StoryStatus.DEVELOPING.value:
        hooks += [
            f"This one is moving right now: {subject}.",
            f"The story on {subject} changed again. Here's where it stands.",
        ]
        if context.conflict_notes:
            hooks.append("Sources are not agreeing on this one yet - here's both sides.")
        else:
            hooks.append(f"New details keep landing on {subject}. Here's what's solid so far.")
    elif status == StoryStatus.FIGHTER_CLAIM.value:
        hooks += [
            f"{subject} just said it themselves - but nobody else has confirmed it.",
            f"Straight from {subject}: here's the claim, and here's what's missing.",
        ]
    elif status == StoryStatus.RUMOR.value:
        hooks += [
            f"There's a rumour going round about {subject}. Let's check what actually backs it up.",
            f"Before you repeat this {subject} rumour - look at where it came from.",
        ]
    else:
        hooks += [
            f"Something is going on with {subject}, and the sourcing is thin so far.",
            f"Keep an eye on this one: {short_claim}.",
        ]
    if context.conflict_notes:
        hooks.append("One source says yes, another says no - here's the actual situation.")
    return hooks[:5]


def build_story_angle(context: StoryContext) -> str:
    status = context.status
    subject = subject_of(context)
    if status == StoryStatus.CONFIRMED.value:
        return (
            f"Lead with the confirmation, then explain what it changes for {subject} and the division. "
            "Your edge is being fast AND correct: say plainly that it is official and who announced it."
        )
    if status == StoryStatus.REPORTED.value:
        return (
            "Lead with who is reporting it, not with the claim itself. The angle is 'this is being "
            "reported, here's how solid the sourcing is, and here's what would make it official'."
        )
    if status == StoryStatus.DEVELOPING.value:
        return (
            "Play it as a live situation. Walk through the timeline in order, show where sources "
            "disagree, and end on what to watch for next rather than a conclusion."
        )
    if status == StoryStatus.FIGHTER_CLAIM.value:
        return (
            f"Frame it as {subject} saying it, not as news. The interesting part is the gap between "
            "the claim and what anyone else has confirmed."
        )
    if status == StoryStatus.RUMOR.value:
        return (
            "Make the sourcing the story. Show where the rumour started, who is repeating it, and "
            "why repetition is not confirmation - that is more useful than the rumour itself."
        )
    return (
        f"Treat it as an early signal on {subject}: say what has been collected, what is missing, "
        "and tell people you will update when more sources land."
    )


def build_questions(context: StoryContext) -> List[str]:
    subject = subject_of(context)
    event = context.events[0] if context.events else "the card"
    questions = [
        "Is this actually official, or just reported?",
        "Who reported it first?",
    ]
    category = context.story.get("category")
    if category == Category.FIGHT_ANNOUNCEMENT.value:
        questions += [f"When and where is it - which card?", "Is it a title fight?",
                      f"What does this mean for the rest of {event}?"]
    elif category == Category.INJURY.value:
        questions += [f"Is {subject} out of their fight?", "How long is the layoff?",
                      "Who replaces them?"]
    elif category in (Category.CANCELLATION.value, Category.REPLACEMENT.value):
        questions += [f"Why was it changed?", f"Is {event} still going ahead?",
                      "Does the new matchup still count for the same stakes?"]
    elif category == Category.RANKING.value:
        questions += ["Who moved up and who dropped?", "Does this change who fights for the title?"]
    elif category == Category.RESULT.value:
        questions += [f"What's next for {subject}?", "Did anything change in the rankings?"]
    else:
        questions += [f"What does this change for {subject}?", "When will we know more?"]
    if context.conflict_notes:
        questions.append("Why are two sources saying different things?")
    questions.append("Where can I read the original reporting?")
    return questions[:8]


def build_script(context: StoryContext, seconds: int = 30) -> str:
    """A spoken-word script built only from what the sources support.

    The opening, the news line, the status caveat and the closing line are
    always kept; optional detail is dropped first when the script must fit.
    """
    status = context.status
    subject = subject_of(context)
    claim = spoken_claim(context.headline)
    independent = context.story.get("independent_source_count") or 0
    hook = build_hooks(context)[0]
    official_names = sorted({source.name for source in context.official_sources})
    credible_names = sorted({source.name for source in context.credible_sources})[:3]

    # Do not say the same sentence twice: if the hook already carries the
    # claim, the news line just adds the sourcing.
    hook_has_claim = _overlaps(hook, claim) or _overlaps(hook, clean_claim(context.headline))
    claim_lead = "" if hook_has_claim else f"{claim}. "
    if status == StoryStatus.CONFIRMED.value and official_names:
        news_line = f"{claim_lead}That's confirmed in official material from {official_names[0]}."
    elif credible_names:
        verb = "are" if len(credible_names) > 1 else "is"
        news_line = f"{claim_lead}That's what {' and '.join(credible_names[:2])} {verb} reporting."
    else:
        news_line = f"{claim_lead}So far that's coming from limited sourcing."

    required: List[str] = [hook, news_line]
    if status != StoryStatus.CONFIRMED.value:
        required.append(
            "The UFC hasn't confirmed it in anything I've collected, so treat it as a report."
        )
    if context.conflict_notes:
        required.append("And the sources don't agree yet - one side is disputing it.")

    closing = (
        f"So that's locked in for {subject}. What do you think happens next?"
        if status == StoryStatus.CONFIRMED.value
        else "I'll update the second it's confirmed - what do you think, real or not?"
    )

    optional: List[str] = []
    gaps = missing_details(context)
    if seconds >= 60:
        if credible_names and status != StoryStatus.CONFIRMED.value:
            optional.append(
                f"Right now it's {independent} independent source"
                f"{'s' if independent != 1 else ''} - aggregators repeating it don't count."
            )
        optional.extend(_spoken_gap(gap, position) for position, gap in enumerate(gaps[:2]))
        timeline_line = _timeline_line(context)
        if timeline_line:
            optional.append(timeline_line)
        optional.append(build_why_it_matters(context))
    elif gaps:
        optional.append(_spoken_gap(gaps[0]))

    max_words = int(seconds * 2.6)
    lines = list(required)
    reserved = len(closing.split())
    while optional:
        candidate = optional.pop(0)
        if _word_count(lines) + len(candidate.split()) + reserved > max_words:
            break
        lines.append(candidate)
    lines.append(closing)
    return collapse_whitespace(" ".join(line.strip() for line in lines if line and line.strip()))


def _word_count(lines: List[str]) -> int:
    return sum(len(line.split()) for line in lines)


def _overlaps(line: str, claim: str, min_chars: int = 30) -> bool:
    """True when a line already contains (most of) the claim."""
    normalized_claim = normalize_text(claim)
    normalized_line = normalize_text(line)
    if not normalized_claim or not normalized_line:
        return False
    probe = normalized_claim[:min_chars]
    if probe and probe in normalized_line:
        return True
    # Headlines get reworded for speech, so also compare on shared wording.
    claim_words = set(normalized_claim.split())
    line_words = set(normalized_line.split())
    if len(claim_words) >= 4:
        shared = len(claim_words & line_words) / len(claim_words)
        return shared >= 0.7
    return False


_GAP_LEAD_INS = [
    "What we still don't have:",
    "Also missing:",
    "Still unanswered:",
]


def _spoken_gap(gap: str, position: int = 0) -> str:
    text = gap.rstrip(".")
    lowered = text[0].lower() + text[1:] if text else text
    lowered = lowered.replace("the collected sources", "anything I've seen")
    lead_in = _GAP_LEAD_INS[position % len(_GAP_LEAD_INS)]
    return f"{lead_in} {lowered}."


def _timeline_line(context: StoryContext) -> Optional[str]:
    ordered = [source for source in context.sources if source.published_at]
    if len(ordered) < 2:
        return None
    ordered.sort(key=lambda source: source.published_at or "")
    first, last = ordered[0], ordered[-1]
    if first.name == last.name:
        return None
    return f"{first.name} had it first, and {last.name} followed it up after that."


def _trim_to_length(script: str, seconds: int) -> str:
    """Roughly 2.5 spoken words per second, with a little headroom."""
    max_words = int(seconds * 2.6)
    words = script.split()
    if len(words) <= max_words:
        return script
    trimmed = " ".join(words[:max_words])
    for terminator in (".", "!", "?"):
        position = trimmed.rfind(terminator)
        if position > len(trimmed) * 0.6:
            return trimmed[: position + 1]
    return trimmed + "..."


def build_social_post_analysis(post: Dict[str, Any], related: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Describe an X post without inventing the context behind it."""
    related = related or []
    account_type = post.get("account_type") or "UNKNOWN"
    text = post.get("text") or ""
    analysis = {
        "author": f"@{post.get('username') or 'unknown'}",
        "account_type": account_type,
        "posted": format_display(post.get("created_at_source")),
        "what_it_says": truncate(text, 240),
        "what_it_does_not_say": [],
        "related_reporting": [],
        "how_to_treat_it": "",
    }
    if len(sentences(text)) <= 1 and len(text) < 90:
        analysis["what_it_does_not_say"].append(
            "The post is short and does not state what it refers to - do not assume the subject."
        )
    if not post.get("fighters"):
        analysis["what_it_does_not_say"].append("No fighter is named in the post text.")
    if not post.get("events"):
        analysis["what_it_does_not_say"].append("No event is named in the post text.")
    for story in related[:3]:
        analysis["related_reporting"].append(
            f"{story.get('headline')} ({status_badge(story.get('status'))})"
        )
    if account_type == "OFFICIAL":
        analysis["how_to_treat_it"] = "Official account - can serve as confirmation of what it states."
    elif account_type in ("ESTABLISHED_JOURNALIST", "TRUSTED_REPORTER", "MAJOR_NEWS"):
        analysis["how_to_treat_it"] = "Press account - treat as a report, still not official confirmation."
    elif account_type in ("FIGHTER", "COACH_TEAM", "PROMOTER"):
        analysis["how_to_treat_it"] = "First-person claim - attribute it to them, it is not confirmation."
    else:
        analysis["how_to_treat_it"] = (
            "Unclassified account - treat as a signal to investigate, not as a source."
        )
    if not related:
        analysis["related_reporting"].append("No related reporting has been collected for this post yet.")
    return analysis
