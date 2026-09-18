"""Category, hedging, attribution and denial detection."""
from __future__ import annotations

import pytest

from models.types import Category
from processors.categorize import classify_text, extract_attributions, is_promotional


@pytest.mark.parametrize("title,body,expected", [
    ("Jon Jones vs. Tom Aspinall official for UFC 320", "The UFC announced Thursday.",
     Category.FIGHT_ANNOUNCEMENT.value),
    ("Alex Pereira suffered a hand injury", "He is dealing with an injury.", Category.INJURY.value),
    ("UFC 320 main event cancelled after failed drug test", "The bout was scrapped.",
     Category.CANCELLATION.value),
    ("Charles Oliveira steps in as replacement on short notice", "He replaces the injured fighter.",
     Category.REPLACEMENT.value),
    ("Fighter suspended six months by the commission", "A suspension was issued.",
     Category.SUSPENSION.value),
    ("Legend retires after 20 years", "He calls it a career.", Category.RETIREMENT.value),
    ("How to watch UFC 320: start time and odds", "Everything for fight week.", Category.PROMO.value),
    ("Champion says he wants the rematch", "He called out the champion.",
     Category.FIGHTER_STATEMENT.value),
    ("New UFC rankings released", "Several fighters moved up the rankings.", Category.RANKING.value),
])
def test_category_detection(title, body, expected):
    assert classify_text(title, body).category == expected


def test_speculation_scoring():
    hedged = classify_text("Report: fighter reportedly out", "Sources say he may be forced out.")
    firm = classify_text("UFC announces new bout", "The promotion confirmed the matchup.")
    assert hedged.speculation_score > firm.speculation_score
    assert hedged.speculation_score > 0.5
    assert firm.speculation_score == 0.0


def test_official_language_detection():
    assert classify_text("Bout set", "The UFC announced Thursday that it is official.").has_official_language
    assert not classify_text("Bout rumoured", "People think it could happen.").has_official_language


def test_denial_detection():
    signals = classify_text("Fighter denies report", "He disputes the claim and calls it false.")
    assert signals.has_denial is True
    assert signals.category == Category.FIGHTER_STATEMENT.value


def test_attribution_marks_derivative_reporting():
    signals = classify_text("Fighter out of UFC 320",
                            "The fighter is out, according to ESPN.", self_outlet="LowKick MMA")
    assert signals.is_derivative is True
    assert "ESPN" in signals.attribution_outlets


def test_self_attribution_is_not_derivative():
    signals = classify_text("ESPN reports the booking", "The bout is set, according to ESPN.",
                            self_outlet="ESPN MMA")
    assert signals.is_derivative is False


def test_extract_attributions_handles_empty_text():
    assert extract_attributions("") == []
    assert extract_attributions(None) == []


def test_is_promotional():
    assert is_promotional("UFC 320 predictions and betting odds") is True
    assert is_promotional("Jon Jones vs. Tom Aspinall official") is False
