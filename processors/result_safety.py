"""What an article is *doing* - and the hard rule that previews and
predictions never become results.

The failure this prevents:

    "UFC 331 predictions: who wins Van vs Pantoja 2?"

is a prediction piece.  Read carelessly it names two fighters, a card and an
apparent outcome, and the app turns it into a fight result - which then
"completes" an event that has not happened.  Ahead of a big card, prediction
and preview pieces outnumber every other kind of coverage, so this is the
single most likely way for fiction to enter the database.

Classification is keyword/rule based so it can explain itself, and the
forbidden path is enforced structurally rather than by scoring: whatever else
a PREVIEW or PREDICTION says, ``may_establish_result`` is False for it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from models.types import (
    ArticleIntent,
    RESULT_BEARING_INTENTS,
    RESULT_FORBIDDEN_INTENTS,
    SourceType,
)
from utils.textutil import normalize_text

#: Source types trusted to establish that a fight or event concluded.
RELIABLE_RESULT_SOURCES = {
    SourceType.OFFICIAL.value,
    SourceType.MAJOR_NEWS.value,
    SourceType.ESTABLISHED_JOURNALIST.value,
    SourceType.TRUSTED_REPORTER.value,
}

# Ordered strongest-signal-first. The first bucket whose pattern hits the
# *headline* decides, because headlines state intent far more reliably.
_PREDICTION_TERMS = [
    "prediction", "predictions", "predicts", "who wins", "pick em", "picks",
    "best bets", "betting odds", "odds preview", "expert picks", "staff picks",
    "how to bet", "prop bets", "parlay", "fantasy preview", "breakdown and prediction",
    "preview and prediction", "forecast", "crystal ball", "we predict",
]
_PREVIEW_TERMS = [
    "preview", "what to expect", "everything you need to know", "how to watch",
    "start time", "start times", "fight card", "full card", "weigh in results",
    "weigh-in results", "ceremonial weigh", "press conference", "media day",
    "countdown", "embedded", "embedded episode", "tale of the tape",
    "keys to victory", "what's at stake", "on the line", "set for", "will face",
]
_RESULT_TERMS = [
    "results", "final results", "full results", "defeats", "defeated", "beats",
    "beat", "knocks out", "knocked out", "submits", "submitted", "taps out",
    "wins by", "won by", "decision win", "unanimous decision", "split decision",
    "majority decision", "tko victory", "ko victory", "finishes", "finished",
    "retains title", "retained", "becomes champion", "new champion", "dethrones",
    "upsets", "highlights", "recap", "winner", "wins the", "claims the title",
]
_POST_FIGHT_TERMS = [
    "post fight", "post-fight", "after the fight", "reacts to loss", "reacts to win",
    "octagon interview", "post fight press conference", "bonus winners",
    "performance of the night", "fight of the night", "medical suspensions",
    "what's next for", "in the aftermath",
]
_INTERVIEW_TERMS = [
    "interview", "speaks out", "opens up", "tells", "explains why", "responds to",
    "fires back", "q&a", "sits down with", "reveals",
]
_ANNOUNCEMENT_TERMS = [
    "announced", "announces", "official", "officially", "booked", "signs",
    "signed", "targeted for", "set for", "added to", "confirms", "confirmed",
    "makes it official", "is official",
]
_RUMOR_TERMS = [
    "rumor", "rumour", "rumored", "reportedly", "report:", "sources say",
    "in talks", "could face", "may face", "linked to", "eyeing", "targeting",
]

#: Verbs describing an outcome that has occurred.  Headlines routinely use the
#: historic present ("Van defeats Pantoja"), so both tenses count - the
#: future-tense guard below is what keeps "will defeat" out.
_RESULT_VERB_RE = re.compile(
    r"\b(defeat(?:s|ed)?|beat(?:s|en)?|knock(?:s|ed)?\s+out|submit(?:s|ted)?|"
    r"won|wins|lost|loses|retain(?:s|ed)?|dethrone(?:s|d)?|finish(?:es|ed)?|"
    r"stop(?:s|ped)?|outpoint(?:s|ed)?|tap(?:s|ped)?\s+out|upset(?:s)?|"
    r"claim(?:s|ed)?\s+the\s+title|becomes\s+champion)\b", re.IGNORECASE)

#: Future/conditional markers that rule a result out however it is worded.
_FUTURE_RE = re.compile(
    r"\b(will|would|could|should|can|might|may|set to|expected to|ahead of|"
    r"upcoming|preview|prediction|predict|scheduled for|takes place|"
    r"this weekend|on saturday|tonight)\b", re.IGNORECASE)

_QUESTION_RE = re.compile(r"\?\s*$")


@dataclass
class IntentResult:
    intent: str = ArticleIntent.UNKNOWN.value
    reasons: List[str] = field(default_factory=list)
    scores: Dict[str, int] = field(default_factory=dict)

    @property
    def may_establish_result(self) -> bool:
        """The structural guard. Previews and predictions can never pass."""
        if self.intent in RESULT_FORBIDDEN_INTENTS:
            return False
        return self.intent in RESULT_BEARING_INTENTS


def _score(terms: List[str], title: str, body: str) -> tuple:
    score, matched = 0, []
    for term in terms:
        normalized = normalize_text(term)
        if not normalized:
            continue
        if normalized in title:
            score += 3
            matched.append(term)
        elif normalized in body:
            score += 1
            matched.append(term)
    return score, matched


def classify_intent(title: str, body: str = "") -> IntentResult:
    """Decide what an article is doing. Headline signals outrank body signals."""
    title_text = normalize_text(title)
    body_text = normalize_text(body)
    raw_title = str(title or "")
    result = IntentResult()
    reasons: List[str] = []

    buckets = {
        ArticleIntent.PREDICTION.value: _score(_PREDICTION_TERMS, title_text, body_text),
        ArticleIntent.PREVIEW.value: _score(_PREVIEW_TERMS, title_text, body_text),
        ArticleIntent.RESULT.value: _score(_RESULT_TERMS, title_text, body_text),
        ArticleIntent.POST_FIGHT.value: _score(_POST_FIGHT_TERMS, title_text, body_text),
        ArticleIntent.INTERVIEW.value: _score(_INTERVIEW_TERMS, title_text, body_text),
        ArticleIntent.ANNOUNCEMENT.value: _score(_ANNOUNCEMENT_TERMS, title_text, body_text),
        ArticleIntent.RUMOR.value: _score(_RUMOR_TERMS, title_text, body_text),
    }
    result.scores = {name: value[0] for name, value in buckets.items() if value[0]}

    # --- hard overrides, checked against the headline only -----------------
    prediction_in_title = any(normalize_text(term) in title_text for term in _PREDICTION_TERMS)
    if prediction_in_title:
        result.intent = ArticleIntent.PREDICTION.value
        reasons.append("The headline asks who wins or offers picks/odds - this is a prediction.")
        result.reasons = reasons
        return result

    # "Who wins?" style questions are never reports of an outcome.
    if _QUESTION_RE.search(raw_title) and buckets[ArticleIntent.RESULT.value][0]:
        result.intent = ArticleIntent.PREVIEW.value
        reasons.append("The headline is a question, so it is discussing a fight, not reporting it.")
        result.reasons = reasons
        return result

    preview_in_title = any(normalize_text(term) in title_text for term in _PREVIEW_TERMS)
    future_in_title = bool(_FUTURE_RE.search(raw_title))
    result_verb_in_title = bool(_RESULT_VERB_RE.search(raw_title))

    # A future/conditional headline cannot be a result, even if it uses
    # result vocabulary ("Jones will defeat Aspinall, says coach").
    if future_in_title:
        if result_verb_in_title:
            result.intent = ArticleIntent.PREVIEW.value
            reasons.append(
                "The headline talks about an outcome that has not happened yet "
                "(future/conditional wording), so it cannot report a result.")
            result.reasons = reasons
            return result
        if buckets[ArticleIntent.RESULT.value][0]:
            result.intent = ArticleIntent.PREVIEW.value
            reasons.append(
                "Result wording appears, but the headline is written about something still to come.")
            result.reasons = reasons
            return result
        if preview_in_title:
            result.intent = ArticleIntent.PREVIEW.value
            reasons.append("The headline previews an upcoming card.")
            result.reasons = reasons
            return result

    if preview_in_title and not result_verb_in_title:
        result.intent = ArticleIntent.PREVIEW.value
        reasons.append("The headline uses preview language.")
        result.reasons = reasons
        return result

    # --- otherwise take the best-scoring bucket ---------------------------
    if not result.scores:
        result.intent = ArticleIntent.UNKNOWN.value
        reasons.append("No intent signals found; treated as unclassified.")
        result.reasons = reasons
        return result

    best = max(result.scores.values())
    tied = [name for name, score in result.scores.items() if score == best]
    order = [
        ArticleIntent.PREDICTION.value, ArticleIntent.PREVIEW.value,
        ArticleIntent.POST_FIGHT.value, ArticleIntent.RESULT.value,
        ArticleIntent.RUMOR.value, ArticleIntent.ANNOUNCEMENT.value,
        ArticleIntent.INTERVIEW.value,
    ]
    for candidate in order:
        if candidate in tied:
            result.intent = candidate
            break
    else:
        result.intent = tied[0]

    # A RESULT that never says anything happened is a preview after all.
    if result.intent == ArticleIntent.RESULT.value and not (
            result_verb_in_title or _RESULT_VERB_RE.search(str(body or ""))):
        result.intent = ArticleIntent.PREVIEW.value
        reasons.append(
            "Result keywords appear but nothing is described in the past tense; "
            "treated as a preview so it cannot create a result.")
    else:
        matched = buckets[result.intent][1][:4]
        if matched:
            reasons.append(f"Matched {result.intent.lower()} wording: {', '.join(matched)}.")

    result.reasons = reasons
    return result


def may_establish_result(intent: Optional[str], source_type: Optional[str] = None,
                         require_reliable_source: bool = True) -> bool:
    """Whether an article may create a result / complete an event.

    Two independent gates, both of which must pass:
      1. the article's intent must be RESULT or POST_FIGHT, and
      2. it must come from a source trusted to report outcomes.
    """
    intent_value = str(intent or "").upper()
    if intent_value in RESULT_FORBIDDEN_INTENTS:
        return False
    if intent_value not in RESULT_BEARING_INTENTS:
        return False
    if not require_reliable_source:
        return True
    return str(source_type or "").upper() in RELIABLE_RESULT_SOURCES


def completion_evidence_from_article(article: Dict[str, Any]) -> bool:
    """True when this stored article may count as 'the event happened'."""
    return may_establish_result(article.get("intent"), article.get("source_type"))
