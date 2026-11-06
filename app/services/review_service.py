"""User-scoped, read-only diary views. All numbers are calculated in Python."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import (
    DailyEntry,
    FoodDay,
    MorningIntent,
    User,
    VitaminIntake,
    VitaminLog,
    WeeklyReflection,
    WeightLog,
)
from app.database.repositories import (
    DailyEntryRepository,
    FoodDayRepository,
    MorningIntentRepository,
)
from app.services.time_service import reflection_day, week_start_date


def earliest_date(session: Session, user: User, today: date) -> date:
    dates = [today]
    for model, field in (
        (DailyEntry, DailyEntry.entry_date),
        (MorningIntent, MorningIntent.intention_date),
        (FoodDay, FoodDay.food_date),
        (WeeklyReflection, WeeklyReflection.week_start_date),
        (VitaminIntake, VitaminIntake.vitamin_date),
        (VitaminLog, VitaminLog.vitamin_date),
        (WeightLog, WeightLog.logged_date),
    ):
        first = session.scalar(
            select(func.min(field)).where(model.user_id == user.id, field <= today)
        )
        if first is not None:
            dates.append(first)
    return min(dates)


def validate_date(value: date, today: date, first: date) -> None:
    if not first <= value <= today:
        raise ValueError("date outside diary")


def days(session: Session, user: User, start: date, end: date) -> list[dict]:
    """At most one week, including empty days. Never joins another user's rows."""
    if end < start or (end - start).days > 6:
        raise ValueError("expected a window of at most seven days")
    entries = {
        e.entry_date: e for e in DailyEntryRepository(session).list_in_range(user.id, start, end)
    }
    mornings = {
        m.intention_date: m
        for m in MorningIntentRepository(session).list_in_range(user.id, start, end)
    }
    food = {f.food_date: f for f in FoodDayRepository(session).list_in_range(user.id, start, end)}
    vitamins: dict[date, list[str]] = {}
    for v in session.scalars(
        select(VitaminIntake).where(
            VitaminIntake.user_id == user.id, VitaminIntake.vitamin_date.between(start, end)
        )
    ):
        vitamins.setdefault(v.vitamin_date, []).append(v.item_code)
    legacy = set(
        session.scalars(
            select(VitaminLog.vitamin_date).where(
                VitaminLog.user_id == user.id, VitaminLog.vitamin_date.between(start, end)
            )
        )
    )
    weights = {
        w.logged_date: w.weight_kg
        for w in session.scalars(
            select(WeightLog).where(
                WeightLog.user_id == user.id, WeightLog.logged_date.between(start, end)
            )
        )
    }
    result = []
    for offset in range((end - start).days + 1):
        d = start + timedelta(days=offset)
        e, m, f = entries.get(d), mornings.get(d), food.get(d)
        result.append(
            {
                "date": d.isoformat(),
                "scores": {"day": e.day_score, "mood": e.mood_score, "energy": e.energy_score}
                if e
                else None,
                "note": e.reflection_text if e else None,
                "intentions": [
                    {"text": text, "outcome": outcome}
                    for text, outcome in (
                        (m.main_intention, m.main_outcome),
                        (m.secondary_intention, m.secondary_outcome),
                    )
                    if text
                ]
                if m
                else [],
                "food": {
                    "complete": f.completed_at is not None,
                    "focus": f.focus_rule,
                    "rules": {r.rule_code: r.kept for r in f.results},
                    "triggers": (f.triggers or "").split(",") if f.triggers else [],
                }
                if f
                else None,
                "vitamins": sorted(vitamins.get(d, [])),
                "legacy_vitamins": d in legacy,
                "weight": weights.get(d),
            }
        )
    return result


def has_data(day: dict) -> bool:
    return bool(
        day["scores"]
        or day["intentions"]
        or day["food"]
        or day["vitamins"]
        or day["legacy_vitamins"]
        or day["weight"] is not None
    )


def summary(items: list[dict]) -> dict:
    rated = [d for d in items if d["scores"]]
    outcomes = Counter(i["outcome"] or "unanswered" for d in items for i in d["intentions"])
    food = [d["food"] for d in items if d["food"] and d["food"]["complete"]]
    rules: dict[str, list[int]] = {}
    for f in food:
        for code, kept in f["rules"].items():
            pair = rules.setdefault(code, [0, 0])
            pair[0] += int(kept)
            pair[1] += 1
    return {
        "completed": len(rated),
        "averages": {
            key: round(sum(d["scores"][key] for d in rated) / len(rated), 1)
            for key in ("day", "mood", "energy")
        }
        if rated
        else None,
        "outcomes": dict(outcomes),
        "food_completed": len(food),
        "food_rules": rules,
        "vitamin_confirmed_days": sum(bool(d["vitamins"] or d["legacy_vitamins"]) for d in items),
    }


def week(session: Session, user: User, start: date, *, today: date | None = None) -> dict:
    today = today or reflection_day(user.timezone)
    if start.weekday() != 0:
        raise ValueError("week must start on Monday")
    first = week_start_date(earliest_date(session, user, today))
    validate_date(start, today, first)
    end = min(start + timedelta(days=6), today)
    current = days(session, user, start, end)
    previous = days(session, user, start - timedelta(days=7), start - timedelta(days=1))
    reflection = session.scalar(
        select(WeeklyReflection).where(
            WeeklyReflection.user_id == user.id, WeeklyReflection.week_start_date == start
        )
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "ongoing": end < start + timedelta(days=6),
        "days": current,
        "summary": summary(current),
        "previous": summary(previous),
        "reflection": {
            key: getattr(reflection, key) for key in ("best_event", "energy_drainer", "want_more")
        }
        if reflection
        else None,
        "first": first.isoformat(),
        "today": today.isoformat(),
    }
