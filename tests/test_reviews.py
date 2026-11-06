"""History, evidence validation, per-user cache and Telegram routing contracts."""
# ruff: noqa: F811

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.bot.handlers import build_root_router, review, weekly
from app.bot.keyboards.reply import BTN_HISTORY, BTN_WEEK
from app.bot.review_views import chunks, day_text, week_text
from app.bot.states import CheckinStates
from app.database.models import (
    DailyEntry,
    FoodDay,
    FoodRuleResult,
    MorningIntent,
    User,
    VitaminIntake,
    WeeklyAnalysis,
    WeeklyReflection,
    WeightLog,
)
from app.database.session import session_scope
from app.services import ai_review_service as ai
from app.services import review_service as svc
from app.services.ai_review_evidence import resolve_draft
from app.services.auth_service import NotAuthorizedError
from app.services.time_service import reflection_day
from tests.test_handlers import (  # noqa: F401
    FakeState,
    app_runtime,
    callback,
)

START = date(2026, 10, 26)
TODAY = date(2026, 11, 6)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(svc, "reflection_day", lambda tz: TODAY)
    monkeypatch.setattr(review, "reflection_day", lambda tz: TODAY)
    ai._attempts.clear()


def add_entries(session, user_id, n=3, text="Своя запись"):
    for i in range(n):
        session.add(
            DailyEntry(
                user_id=user_id,
                entry_date=START + timedelta(days=i),
                day_score=4,
                mood_score=3,
                energy_score=2,
                reflection_text=text,
            )
        )
    session.commit()


def good_payload(d=START):
    return {
        "week_start": str(START),
        "through": str(START + timedelta(days=6)),
        "days": [
            {
                "date": str(d),
                "scores": {"day": 4, "mood": 3, "energy": 2},
                "note": None,
                "intentions": [],
            }
        ],
        "weekly_reflection": None,
    }


def good_draft(d=START):
    sources = [{"source_id": f"{d}:scores", "quote": "День: 4/5; настроение: 3/5; энергия: 2/5."}]
    return {
        "observations": [
            {
                "text": "Удачный день может сочетаться с небольшим запасом сил. Стоит заметить, что помогает получать удовлетворение в таком состоянии.",
                "sources": sources,
            }
        ],
        "experiment": {
            "text": "В следующем дневном итоге назови одно событие, сделавшее день удачным.",
            "check": "Отметь, сопровождалось ли это большим запасом сил; отсутствие связи тоже результат.",
            "sources": sources,
        },
        "question": {
            "text": "Что помогает тебе считать день удачным при небольшом запасе сил?",
            "sources": sources,
        },
    }


def good_review(d=START):
    return resolve_draft(json.dumps(good_draft(d)), good_payload(d))


def test_week_counts_missing_days_and_user_isolation(session, user):
    add_entries(session, user.id)
    other = User(telegram_user_id=222, display_name="Чужой")
    session.add(other)
    session.commit()
    add_entries(session, other.id, 7, "НЕ ДОЛЖНО ПОПАСТЬ")
    session.add_all(
        [
            MorningIntent(user_id=user.id, intention_date=START, main_intention="Своё дело"),
            WeeklyReflection(user_id=user.id, week_start_date=START, best_event="Свой итог"),
            WeeklyReflection(user_id=other.id, week_start_date=START, best_event="Чужой итог"),
        ]
    )
    session.commit()
    w = svc.week(session, user, START)
    assert w["summary"]["completed"] == 3
    assert w["summary"]["averages"] == {"day": 4.0, "mood": 3.0, "energy": 2.0}
    assert w["summary"]["outcomes"] == {"unanswered": 1}
    assert w["previous"]["averages"] is None
    assert w["days"][3]["scores"] is None
    assert "Чужой" not in json.dumps(w, ensure_ascii=False)
    assert "НЕ ДОЛЖНО" not in json.dumps(w, ensure_ascii=False)
    assert "Свой итог" in week_text(w)


def test_history_includes_food_only_and_vitamins_without_negative_inference(session, user):
    food = FoodDay(user_id=user.id, food_date=START, focus_rule="no_sugar")
    food.results = [FoodRuleResult(rule_code="no_sugar", kept=False)]
    session.add_all(
        [
            food,
            VitaminIntake(
                user_id=user.id, vitamin_date=START, item_code="omega_3", taken_at=datetime.now(UTC)
            ),
            WeightLog(user_id=user.id, logged_date=START, week_start_date=START, weight_kg=80),
        ]
    )
    session.commit()
    assert svc.earliest_date(session, user, TODAY) == START
    w = svc.week(session, user, START)
    assert w["summary"]["food_completed"] == 0  # Unsubmitted checklist isn't a result.
    assert w["summary"]["food_rules"] == {}
    assert w["summary"]["vitamin_confirmed_days"] == 1
    assert "черновик" in day_text(w["days"][0])
    assert "Омега-3" in day_text(w["days"][0])
    assert svc.has_data(w["days"][0])
    assert not svc.has_data(w["days"][1])
    assert "Нет" not in day_text(w["days"][1])


def test_year_and_five_am_boundaries(session, user, monkeypatch):
    start = date(2025, 12, 29)
    session.add(
        DailyEntry(user_id=user.id, entry_date=start, day_score=1, mood_score=2, energy_score=3)
    )
    session.commit()
    # Monday 04:59 Moscow is still Sunday, completing the previous year's week.
    moment = datetime(2026, 1, 5, 1, 59, tzinfo=UTC)
    monkeypatch.setattr(svc, "reflection_day", lambda tz: reflection_day(tz, moment))
    w = svc.week(session, user, start)
    assert w["end"] == "2026-01-04"
    assert not w["ongoing"]
    with pytest.raises(ValueError):
        svc.week(session, user, date(2026, 1, 5))


def test_payload_excludes_profile_and_health_fields_and_bounds_text(session, user):
    add_entries(session, user.id, text="x" * 5000)
    w = svc.week(session, user, START)
    w["days"][0].update(weight=99.7, vitamins=["omega_3"], food={"secret": True})
    w["telegram_id"] = 111
    payload = ai.payload_for(w)
    raw = json.dumps(payload)
    assert all(key not in raw for key in ("telegram_id", "weight", "vitamins", "food", "omega_3"))
    assert len(payload["days"][0]["note"]) == 1200
    assert len(w["days"][0]["note"]) == 5000  # Stored/displayed diary stays intact.


@pytest.mark.parametrize(
    "bad",
    [
        {"observations": [], "experiment": {"text": "x", "dates": [str(START)]}, "question": "x"},
        {**good_review().model_dump(mode="json"), "unexpected": "x"},
        good_review(START - timedelta(days=1)).model_dump(mode="json"),
        {**good_review().model_dump(mode="json"), "question": " "},
    ],
)
def test_invalid_ai_output_is_rejected(bad):
    with pytest.raises(ValueError):
        ai.validate_result(json.dumps(bad), good_payload())


async def test_cache_concurrency_invalidation_and_users(
    settings, session, session_factory, user, monkeypatch
):
    settings.llm_api_key = SecretStr("fake-secret")
    add_entries(session, user.id)
    calls = []

    async def provider(settings, payload):
        calls.append(payload)
        # A separate writer works while the model is running (no held session).
        with session_scope(session_factory) as s:
            s.get(User, user.id).display_name = "Updated"
        await asyncio.sleep(0.01)
        return good_review()

    monkeypatch.setattr(ai, "request_review", provider)
    results = await asyncio.gather(
        *[ai.analyze(settings, session_factory, 111, START) for _ in range(3)]
    )
    assert [cached for _, cached in results] == [False, True, True]
    assert len(calls) == 1
    assert session.query(WeeklyAnalysis).count() == 1
    with pytest.raises(NotAuthorizedError):
        await ai.analyze(settings, session_factory, 999, START)
    with pytest.raises((ValueError, ai.AnalysisUnavailable)):
        await ai.analyze(settings, session_factory, 222, START)
    assert len(calls) == 1  # Never falls back to another user's cached review.
    entry = session.scalar(select(DailyEntry).where(DailyEntry.user_id == user.id))
    entry.reflection_text = "Изменённая запись"
    session.commit()
    ai._attempts.clear()
    _, cached = await ai.analyze(settings, session_factory, 111, START)
    assert not cached and len(calls) == 2
    assert session.query(WeeklyAnalysis).count() == 1


async def test_changed_input_during_request_is_not_saved(
    settings, session, session_factory, user, monkeypatch
):
    settings.llm_api_key = SecretStr("fake-secret")
    add_entries(session, user.id)

    async def provider(*args):
        with session_scope(session_factory) as s:
            s.scalar(select(DailyEntry)).reflection_text = "Изменилось во время анализа"
        return good_review()

    monkeypatch.setattr(ai, "request_review", provider)
    with pytest.raises(ai.AnalysisUnavailable, match="изменились"):
        await ai.analyze(settings, session_factory, 111, START)
    assert session.query(WeeklyAnalysis).count() == 0


async def test_insufficient_data_and_provider_failure_not_cached(
    settings, session, session_factory, user, monkeypatch
):
    settings.llm_api_key = SecretStr("fake-secret")
    add_entries(session, user.id, 2)
    mock = AsyncMock(side_effect=ai.AnalysisUnavailable("Недоступно"))
    monkeypatch.setattr(ai, "request_review", mock)
    with pytest.raises(ai.AnalysisUnavailable, match="три"):
        await ai.analyze(settings, session_factory, 111, START)
    mock.assert_not_called()
    session.add(
        DailyEntry(
            user_id=user.id,
            entry_date=START + timedelta(days=2),
            day_score=3,
            mood_score=4,
            energy_score=5,
        )
    )
    session.commit()
    with pytest.raises(ai.AnalysisUnavailable, match="Недоступно"):
        await ai.analyze(settings, session_factory, 111, START)
    with pytest.raises(ai.AnalysisUnavailable, match="15 секунд"):
        await ai.analyze(settings, session_factory, 111, START)
    assert mock.await_count == 1 and session.query(WeeklyAnalysis).count() == 0


@pytest.mark.parametrize("failure", [None, "429", "timeout", "json", "truncated", "large"])
@pytest.mark.parametrize("model", ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"])
async def test_http_contract_bounded_response_and_safe_logs(
    settings, monkeypatch, caplog, failure, model
):
    settings.llm_model = model
    settings.llm_api_key = SecretStr("DO-NOT-LOG-KEY")
    content = good_review().model_dump_json()
    body = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "length" if failure == "truncated" else "stop",
                    "message": {"content": content},
                }
            ]
        }
    ).encode()
    if failure == "json":
        body = b"PRIVATE-JOURNAL"
    if failure == "large":
        body = b"x" * 70000

    class Stream:
        async def iter_chunked(self, n):
            for i in range(0, len(body), 7):  # Real response can arrive in tiny pieces.
                yield body[i : i + 7]

    class Response:
        status = 429 if failure == "429" else 200
        content = Stream()

        def __init__(self):
            self.headers = {}

        async def __aenter__(self):
            if failure == "timeout":
                raise TimeoutError("PRIVATE-JOURNAL DO-NOT-LOG-KEY")
            return self

        async def __aexit__(self, *args):
            pass

    class Client:
        def __init__(self, *, timeout):
            assert timeout.total == settings.llm_timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def post(self, url, *, headers, json, allow_redirects):
            assert url == "https://api.groq.com/openai/v1/chat/completions"
            assert headers["Authorization"] == "Bearer DO-NOT-LOG-KEY"
            assert not allow_redirects
            assert json["response_format"]["type"] == "json_schema"
            assert json["response_format"]["json_schema"]["strict"] is True
            if model.startswith("openai/"):
                assert json["reasoning_effort"] == "medium"
            return Response()

    monkeypatch.setattr(ai.aiohttp, "ClientSession", Client)
    if failure:
        with pytest.raises(ai.AnalysisUnavailable) as err:
            await ai._completion(settings, ai.SYSTEM_PROMPT, good_payload(), tokens=3500)
        assert "PRIVATE-JOURNAL" not in str(err.value)
    else:
        assert (
            await ai._completion(settings, ai.SYSTEM_PROMPT, good_payload(), tokens=3500) == content
        )
    assert "DO-NOT-LOG-KEY" not in caplog.text
    assert "PRIVATE-JOURNAL" not in caplog.text


async def test_credential_never_goes_to_another_host(settings):
    settings.llm_base_url = "https://example.com/openai/v1"
    with pytest.raises(ai.AnalysisUnavailable, match="адрес API Groq"):
        await ai.request_review(settings, good_payload())


async def test_database_failure_does_not_log_model_response(settings, monkeypatch, caplog):
    monkeypatch.setattr(ai, "_analyze", AsyncMock(side_effect=SQLAlchemyError("PRIVATE-REVIEW")))
    with pytest.raises(ai.AnalysisUnavailable) as err:
        await ai.analyze(settings, None, 111, START)
    assert "PRIVATE-REVIEW" not in str(err.value) + caplog.text


def test_long_unicode_diary_is_not_cut_or_interpreted():
    text = "<b>личный текст</b>😀" * 1000
    parts = chunks(text)
    assert "".join(parts) == text
    assert all(len(p.encode("utf-16-le")) // 2 <= 3500 for p in parts)


async def test_history_callbacks_use_actor_and_validate_input(app_runtime, session, user):
    add_entries(session, user.id)
    for value in ("rv:d:2026-10-26", "rv:w:2026-10-26", "rv:days:0", "rv:weeks:0"):
        cb = callback(value)
        await review.browse(cb)
        assert cb.answered == 1 and cb.message.edits
    assert session.query(User).filter_by(telegram_user_id=777).count() == 0
    for value in (
        "rv:d:2099-01-01",
        "rv:w:no-date",
        "rv:days:-1",
        "rv:weeks:99999",
        "rv:unknown:0",
    ):
        cb = callback(value)
        await review.browse(cb)
        assert cb.message.answers == [review.INVALID_DATE]
    state = FakeState({"best_event": "старый ответ"})
    await weekly.start_weekly(callback("wk:start:not-a-date"), state)
    assert state.data == {"best_event": "старый ответ"}
    await weekly.start_weekly(callback(f"wk:start:{START}"), state)
    assert "best_event" not in state.data


async def test_ai_callback_renders_bound_sources_and_uses_callback_user(app_runtime, monkeypatch):
    analyze = AsyncMock(return_value=(good_review(), True))
    monkeypatch.setattr(ai, "analyze", analyze)
    cb = callback(f"rv:a:{START}")
    await review.browse(cb)
    analyze.assert_awaited_once_with(app_runtime.settings, app_runtime.session_factory, 111, START)
    rendered = "".join(cb.message.answers)
    assert "Сохранённый обзор" in rendered
    assert "Запись 26.10.2026 · Оценки: «День: 4/5" in rendered
    assert "Что отметить:" in rendered
    assert good_review().question.text in rendered


async def test_real_dispatcher_browses_during_fsm_without_saving_menu_text(
    app_runtime, session, user, monkeypatch
):
    add_entries(session, user.id)
    root = build_root_router()
    dp = Dispatcher()
    dp.include_router(root)
    bot = Bot("777:FAKE_TOKEN_FOR_TESTING")
    sent = []

    async def transport(bot, method, **kwargs):
        sent.append(method)
        return True

    monkeypatch.setattr(bot.session, "make_request", transport)
    state = dp.fsm.get_context(bot=bot, chat_id=111, user_id=111)
    await state.set_state(CheckinStates.waiting_reflection_text)
    await state.update_data(day_score=5, mood_score=5, energy_score=5)
    try:
        for i, text in enumerate(
            [BTN_WEEK, BTN_HISTORY, "/week 2026-10-26", "/day 2026-10-26", "/history"]
        ):
            await dp.feed_update(
                bot,
                Update(
                    update_id=i,
                    message=Message(
                        message_id=i + 1,
                        date=datetime.now(UTC),
                        chat=Chat(id=111, type="private"),
                        from_user=TgUser(id=111, is_bot=False, first_name="Test"),
                        text=text,
                    ),
                ),
            )
            assert await state.get_state() == CheckinStates.waiting_reflection_text.state
        assert len(sent) == 5
        assert all(m.parse_mode is None for m in sent)
        assert session.query(DailyEntry).count() == 3
    finally:
        for child in root.sub_routers:
            child._parent_router = None  # Other existing tests assemble the global routers too.
        await dp.storage.close()
        await bot.session.close()
