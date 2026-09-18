"""AI layer: template mode, grounding, caching and provider fallback."""
from __future__ import annotations

import json

from ai import service as ai_service
from ai.context import build_story_context
from ai.factory import get_provider
from ai.grounding import check_grounding
from ai.provider import AIResponse
from ai.providers.anthropic_provider import AnthropicProvider
from ai.providers.openai_provider import OpenAIProvider
from database import repo_ai as ai_repo
from database import repo_stories as stories_repo
from processors import pipeline
from tests.conftest import hours_ago
from tests.fake_http import FakeHttpClient
from utils.http import HttpResult


def _story(ingest, make_article):
    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     source_name="UFC.com", source_type="OFFICIAL", is_official=True,
                     reliability_weight=1.0, independence_group="ufc_official",
                     excerpt="The UFC announced the heavyweight title fight for UFC 320."),
        make_article(title="Jones vs. Aspinall set for UFC 320", source_name="ESPN",
                     independence_group="espn",
                     excerpt="The heavyweight title fight is official for UFC 320."),
    ])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1)[0]
    pipeline.recompute_story(int(story["id"]))
    return int(story["id"])


def test_app_works_without_an_api_key():
    status = ai_service.ai_status()
    assert status["configured"] is False
    assert status["active"] == "template"
    assert get_provider().is_configured is False


def test_template_summary_is_grounded(ingest, make_article):
    story_id = _story(ingest, make_article)
    generated = ai_service.summarize_story(story_id)
    assert generated.provider == "template"
    assert "Template mode" in generated.notice
    assert "UFC 320" in generated.text
    assert generated.sources, "generated text must carry its sources"
    context = build_story_context(story_id)
    report = check_grounding(generated.text, context.source_text)
    assert report.unsupported_quotes == []


def test_scripts_respect_length_and_status(ingest, make_article):
    story_id = _story(ingest, make_article)
    script_30 = ai_service.generate_tiktok_script(story_id, 30)
    script_60 = ai_service.generate_tiktok_script(story_id, 60)
    assert 20 <= len(script_30.text.split()) <= 90
    assert len(script_60.text.split()) >= len(script_30.text.split())
    assert "official" in script_30.text.lower()
    assert script_30.text.strip().endswith(("?", ".", "!"))


def test_rumor_script_never_claims_confirmation(ingest, make_article):
    ingest([make_article(title="Rumour: Jon Jones could retire after UFC 320",
                         source_name="Fan Site", source_type="FAN_ACCOUNT",
                         reliability_weight=0.15,
                         excerpt="Speculation suggests he may walk away, according to nobody official.")])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1)[0]
    pipeline.recompute_story(int(story["id"]))
    script = ai_service.generate_tiktok_script(int(story["id"]), 30).text.lower()
    assert "it's official" not in script
    assert "treat it as a report" in script or "rumour" in script or "limited sourcing" in script


def test_reporting_check_sections_are_complete(ingest, make_article):
    story_id = _story(ingest, make_article)
    check = ai_service.generate_reporting_check(story_id)
    for heading in ("confirmed_facts", "reported_claims", "unconfirmed", "conflicting",
                    "missing", "potential_mistakes"):
        assert heading in check.sections
        assert check.sections[heading]


def test_hooks_angle_and_questions(ingest, make_article):
    story_id = _story(ingest, make_article)
    hooks = ai_service.generate_tiktok_hooks(story_id)
    questions = ai_service.generate_questions(story_id)
    angle = ai_service.generate_story_angle(story_id)
    assert 3 <= len(hooks.items) <= 6
    assert all(len(hook.split()) <= 25 for hook in hooks.items)
    assert len(questions.items) >= 4
    assert len(angle.text.split()) > 10


def test_key_facts_come_from_the_database(ingest, make_article):
    story_id = _story(ingest, make_article)
    facts = ai_service.key_facts(story_id)
    assert any("Status:" in fact for fact in facts)
    assert any("Sources:" in fact for fact in facts)


def test_generation_is_cached(ingest, make_article):
    story_id = _story(ingest, make_article)
    first = ai_service.summarize_story(story_id)
    second = ai_service.summarize_story(story_id)
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.text == first.text
    assert ai_repo.latest_summary(story_id, "summary") is not None


def test_grounding_flags_invented_quotes_and_figures():
    sources = "The UFC announced the bout. He said I am ready for anyone."
    clean = check_grounding("The UFC announced the bout on Thursday.", sources)
    assert clean.ok is True
    dirty = check_grounding('He said "I will knock him out in round one" and is 27-1.', sources)
    assert dirty.ok is False
    assert dirty.unsupported_quotes
    assert "27-1" in dirty.unsupported_figures


def test_deterministic_helpers_work_without_ai(ingest, make_article):
    story_id = _story(ingest, make_article)
    assert ai_service.classify_story("Fighter out with injury")["category"] == "injury"
    assert ai_service.identify_fighters("Jon Jones is back") == ["Jon Jones"]
    assert ai_service.identify_event("Booked for UFC 320") == "UFC 320"
    support = ai_service.assess_source_support(story_id)
    assert support["label"] and "NOT a probability" in support["disclaimer"]
    rumor = ai_service.detect_rumor(story_id)
    assert rumor["is_rumor"] is False
    relevance = ai_service.calculate_relevance(story_id)
    assert relevance["score"] > 0 and relevance["breakdown"]


def test_provider_errors_fall_back_to_templates(monkeypatch, ingest, make_article):
    story_id = _story(ingest, make_article)

    class BrokenProvider:
        name = "anthropic"
        model = "claude-sonnet-5"
        is_configured = True

        def generate(self, *args, **kwargs):
            return AIResponse(provider="anthropic", error="boom", error_kind="http")

    monkeypatch.setattr(ai_service, "get_provider", lambda: BrokenProvider())
    generated = ai_service.summarize_story(story_id, force_refresh=True)
    assert generated.provider == "template"
    assert "AI provider unavailable" in generated.notice
    assert generated.text


def test_anthropic_provider_parses_a_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from utils.config import get_config

    get_config(refresh=True)
    payload = json.dumps({"content": [{"type": "text", "text": "A grounded summary."}],
                          "usage": {"input_tokens": 10, "output_tokens": 5}})
    provider = AnthropicProvider(client=FakeHttpClient(default=HttpResult(
        ok=True, status_code=200, url="u", text=payload, content=payload.encode())))
    provider.http.post_json = lambda url, payload_, headers=None, **kw: HttpResult(  # type: ignore
        ok=True, status_code=200, url=url, text=payload, content=payload.encode())
    response = provider.generate("prompt")
    assert response.ok and response.text == "A grounded summary."
    assert response.tokens_in == 10


def test_openai_provider_reports_auth_failure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from utils.config import get_config

    get_config(refresh=True)
    provider = OpenAIProvider()
    provider.http.post_json = lambda url, payload_, headers=None, **kw: HttpResult(  # type: ignore
        status_code=401, url=url, error="HTTP 401", error_kind="http")
    response = provider.generate("prompt")
    assert response.ok is False
    assert response.error_kind == "auth"


def test_social_post_analysis_does_not_invent_context():
    from database import repo_social as social_repo

    post_id = social_repo.upsert_post({
        "post_id": "vague", "username": "somefighter", "account_type": "FIGHTER",
        "text": "Can't believe this.", "created_at_source": hours_ago(1),
    })
    analysis = ai_service.analyze_social_post(int(post_id))
    assert "first-person claim" in analysis["how_to_treat_it"].lower()
    assert any("does not state" in line or "No fighter is named" in line
               for line in analysis["what_it_does_not_say"])
    assert "No related reporting" in " ".join(analysis["related_reporting"])
