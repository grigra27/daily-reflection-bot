"""Regressions for source/date confusion, unsupported prose and quality gates."""

import asyncio
import copy
import json
from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest

from app.bot.review_views import analysis_text
from app.database.models import WeeklyAnalysis
from app.services import ai_review_service as ai
from app.services.ai_review_evidence import (
    contrasts_for,
    resolve_draft,
    sources_for,
    validate_cached,
)
from tests.test_reviews import START, add_entries, good_draft, good_payload, good_review


def activity_payload():
    payload = good_payload()
    payload["days"] += [
        {
            "date": "2026-10-31",
            "scores": {"day": 4, "mood": 4, "energy": 3},
            "note": "Гулял с друзьями у озера.",
            "intentions": [],
        },
        {
            "date": "2026-11-01",
            "scores": {"day": 5, "mood": 4, "energy": 2},
            "note": None,
            "intentions": [{"text": "Сходить на занятие по рисованию", "outcome": "done"}],
        },
    ]
    return payload


def test_wrong_event_date_is_rejected_even_when_both_dates_exist():
    payload = activity_payload()
    draft = good_draft()
    draft["observations"][0]["sources"] = [
        {
            "source_id": "2026-10-31:note",
            "quote": "Сходить на занятие по рисованию",
        }
    ]
    with pytest.raises(ValueError, match="exact fragment"):
        resolve_draft(json.dumps(draft), payload)
    # Correct source: date and completion status are attached by code.
    draft["observations"][0]["sources"][0]["source_id"] = "2026-11-01:intent0"
    resolved = resolve_draft(json.dumps(draft), payload)
    ref = resolved.observations[0].sources[0]
    assert ref.date == date(2026, 11, 1)
    assert ref.label == "Намерение — выполнено"
    assert "Запись 01.11.2026 · Намерение — выполнено" in analysis_text(
        resolved, START, cached=False
    )


@pytest.mark.parametrize(
    "change", ["unknown", "invented", "too_short", "duplicate", "injected_date"]
)
def test_sources_cannot_be_invented_or_rebound(change):
    draft = good_draft()
    ref = draft["observations"][0]["sources"][0]
    if change == "unknown":
        ref["source_id"] = "another-user:note"
    elif change == "invented":
        ref["quote"] = "День: 5/5; настроение: 5/5; энергия: 5/5."
    elif change == "too_short":
        ref["quote"] = "День"
    elif change == "duplicate":
        draft["observations"][0]["sources"].append(copy.deepcopy(ref))
    else:
        ref["date"] = "2026-10-31"
    with pytest.raises(ValueError):
        resolve_draft(json.dumps(draft), good_payload())


@pytest.mark.parametrize(
    "text",
    [
        "Занятие было 31 октября.",
        "В субботу состоялось занятие.",
        "Вчера ты был бодрее.",
        "Энергия поднялась до 5/5.",
        "26.10 произошла встреча.",
    ],
)
def test_chronology_and_numbers_cannot_be_rewritten_in_model_prose(text):
    draft = good_draft()
    draft["observations"][0]["text"] = text
    with pytest.raises(ValueError):
        resolve_draft(json.dumps(draft), good_payload())


@pytest.mark.parametrize(
    "text",
    [
        "Что важнее: работа или хобби?",
        "Выбери главное направление?",
        "Что помогло? Что мешало?",
    ],
)
def test_forced_choices_and_multiple_questions_are_rejected(text):
    draft = good_draft()
    draft["question"]["text"] = text
    with pytest.raises(ValueError):
        resolve_draft(json.dumps(draft), good_payload())


def test_unknown_intention_outcome_and_weekly_time_context_are_preserved():
    payload = good_payload()
    payload["days"][0]["intentions"] = [{"text": "Подготовить материалы", "outcome": None}]
    payload["weekly_reflection"] = {"best_event": "Приятная встреча с друзьями"}
    sources = sources_for(payload)
    assert sources[f"{START}:intent0"]["label"] == "Намерение — итог не отмечен"
    draft = good_draft()
    draft["question"]["sources"] = [
        {"source_id": "week:best_event", "quote": "Приятная встреча с друзьями"}
    ]
    result = resolve_draft(json.dumps(draft), payload)
    assert result.question.dates == []  # Weekly prose must not claim a Monday event.
    assert "Неделя с 26.10.2026" in analysis_text(result, START, cached=False)


def test_scores_are_computed_and_missing_days_are_not_adjacent():
    payload = activity_payload()
    contrasts = contrasts_for(payload)
    assert {
        "kind": "day_energy_gap",
        "sources": ["2026-11-01:scores"],
        "day_score": 5,
        "energy_score": 2,
    } in contrasts
    assert not any(c["kind"] == "adjacent_score_change" for c in contrasts)
    payload["days"][-1]["scores"]["mood"] = 2
    assert {
        "kind": "adjacent_score_change",
        "metric": "mood",
        "sources": ["2026-10-31:scores", "2026-11-01:scores"],
        "before": 4,
        "after": 2,
    } in contrasts_for(payload)


def test_cache_rechecks_exact_quotes_dates_and_status():
    raw = good_review().model_dump_json()
    assert validate_cached(raw, good_payload()) == good_review()
    value = json.loads(raw)
    value["observations"][0]["sources"][0]["date"] = "2026-10-31"
    with pytest.raises(ValueError):
        validate_cached(json.dumps(value), good_payload())
    value = json.loads(raw)
    value["observations"][0]["sources"][0]["label"] = "Придуманное событие"
    with pytest.raises(ValueError):
        validate_cached(json.dumps(value), good_payload())


async def test_pipeline_checks_meaning_and_repairs_once(settings, monkeypatch):
    draft = json.dumps(good_draft())
    completion = AsyncMock(
        side_effect=[
            draft,
            '{"approved":false,"issues":["retelling","leading_measurement"]}',
            draft,
            '{"approved":true,"issues":[]}',
        ]
    )
    monkeypatch.setattr(ai, "_completion", completion)
    assert await ai.request_review(settings, good_payload()) == good_review()
    assert completion.await_count == 4
    assert completion.await_args_list[1].args[1] == ai.CRITIC_PROMPT
    retry_input = completion.await_args_list[2].args[2]
    assert retry_input["revision_required"] == ["retelling", "leading_measurement"]
    assert "previous_draft" not in retry_input  # Keep retries within the shared token budget.


async def test_invalid_quote_is_repaired_before_semantic_check(settings, monkeypatch):
    bad = good_draft()
    bad["observations"][0]["sources"][0]["quote"] = "Неподтверждённая цитата из чужого дня"
    completion = AsyncMock(
        side_effect=[
            json.dumps(bad),
            json.dumps(good_draft()),
            '{"approved":true,"issues":[]}',
        ]
    )
    monkeypatch.setattr(ai, "_completion", completion)
    assert await ai.request_review(settings, good_payload()) == good_review()
    assert completion.await_count == 3


@pytest.mark.parametrize(
    "verdict",
    [
        '{"approved":false,"issues":["wrong_chronology"]}',
        '{"approved":true,"issues":["unsupported_claim"]}',
        '{"approved":false,"issues":[]}',
    ],
)
async def test_unapproved_review_never_leaves_pipeline(settings, monkeypatch, verdict):
    completion = AsyncMock(side_effect=[json.dumps(good_draft()), verdict] * 2)
    monkeypatch.setattr(ai, "_completion", completion)
    with pytest.raises(ai.AnalysisUnavailable, match="проверку"):
        await ai.request_review(settings, good_payload())
    assert completion.await_count == 4


async def test_one_deadline_covers_all_model_calls(settings, monkeypatch):
    settings.llm_timeout = 0.12
    calls = 0

    async def slow(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.08)
        return json.dumps(good_draft()) if calls == 1 else '{"approved":true,"issues":[]}'

    monkeypatch.setattr(ai, "_completion", slow)
    with pytest.raises(ai.AnalysisUnavailable):
        await ai.request_review(settings, good_payload())
    assert calls == 2


async def test_provider_retry_after_is_obeyed_once(settings, monkeypatch):
    once = AsyncMock(side_effect=[ai._RateLimited(0.5), "ok"])
    sleep = AsyncMock()
    monkeypatch.setattr(ai, "_completion_once", once)
    monkeypatch.setattr(ai.asyncio, "sleep", sleep)
    assert await ai._completion(settings, "system", {}, tokens=100) == "ok"
    assert once.await_count == 2
    sleep.assert_awaited_once_with(0.75)


@pytest.mark.parametrize("delay,expected_calls", [(None, 1), (0.5, 2)])
async def test_rate_limit_retry_cannot_loop(settings, monkeypatch, delay, expected_calls):
    once = AsyncMock(side_effect=ai._RateLimited(delay))
    monkeypatch.setattr(ai, "_completion_once", once)
    monkeypatch.setattr(ai.asyncio, "sleep", AsyncMock())
    with pytest.raises(ai.AnalysisUnavailable, match="лимита"):
        await ai._completion(settings, "system", {}, tokens=100)
    assert once.await_count == expected_calls


async def test_rate_limit_wait_obeys_pipeline_deadline(settings, monkeypatch):
    settings.llm_timeout = 0.04
    once = AsyncMock(side_effect=ai._RateLimited(0.01))
    monkeypatch.setattr(ai, "_completion_once", once)
    with pytest.raises(ai.AnalysisUnavailable):
        await ai.request_review(settings, good_payload())
    assert once.await_count == 1


async def test_old_cache_regenerated_and_replaced_only_after_acceptance(
    settings, session, session_factory, user, monkeypatch
):
    from pydantic import SecretStr

    settings.llm_api_key = SecretStr("fake-secret")
    add_entries(session, user.id)
    previous_time = datetime(2026, 1, 1)  # noqa: DTZ001 -- SQLite returns naive UTC.
    old = WeeklyAnalysis(
        user_id=user.id,
        week_start=START,
        input_hash="old-v1-hash",
        model=settings.llm_model,
        result_json='{"old":"review"}',
        created_at=previous_time,
    )
    session.add(old)
    session.commit()
    ai._attempts.clear()
    monkeypatch.setattr(
        ai, "request_review", AsyncMock(side_effect=ai.AnalysisUnavailable("failed"))
    )
    with pytest.raises(ai.AnalysisUnavailable):
        await ai.analyze(settings, session_factory, 111, START)
    session.refresh(old)
    assert old.result_json == '{"old":"review"}'
    ai._attempts.clear()
    monkeypatch.setattr(ai, "request_review", AsyncMock(return_value=good_review()))
    result, cached = await ai.analyze(settings, session_factory, 111, START)
    session.refresh(old)
    assert not cached and result == good_review()
    assert old.created_at > previous_time
    assert ai.validate_result(old.result_json, good_payload()) == good_review()
    assert session.query(WeeklyAnalysis).count() == 1
