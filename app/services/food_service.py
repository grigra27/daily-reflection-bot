"""Food reflection business logic (v1.3).

No calorie or macro counting: a day is described by a small set of the user's
own yes/no rules, an optional morning focus (one of those rules) and, when a
rule was broken, optional trigger tags. Weight is a separate, weekly number.

Rules and triggers are identified by stable string codes. Their Russian labels
are presentation and live in ``app.bot.texts``; only codes are stored, sent in
callback data and exported. Dates are always the user's Reflection Day
(05:00 rollover), frozen into callback data by the bot layer.

Evening semantics: the checklist asks "what did you break today?". Every rule
is *kept* unless the user marks it; draft marks are persisted on each tap so a
restart never loses them, and ``completed_at`` marks the submitted evening. A
submitted day is read-only until the user explicitly reopens it — a stale or
replayed button tap cannot quietly rewrite it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.database.models import DailyEntry, FoodDay, User, WeightLog
from app.database.repositories import FoodDayRepository, UserRepository, WeightLogRepository
from app.services.time_service import reflection_day, week_start_date

# --- Catalog -------------------------------------------------------------------
#: Every rule the bot knows, in display order. Codes are persisted: never rename
#: or reuse one; add new codes instead.
RULE_CODES: tuple[str, ...] = (
    "no_late_eating",
    "no_fastfood",
    "no_sweets",
    "no_chips",
    "no_sugary_drinks",
    "no_alcohol",
    "stop_when_full",
    "no_screen",
    "no_packaging",
    "water",
    "no_after_20",
    "no_snacks",
    "breakfast",
    "vegetables",
    "no_seconds",
    "eat_slowly",
    "steps",
)

#: The set a user starts with (``users.food_rules`` is NULL).
DEFAULT_RULES: tuple[str, ...] = (
    "no_late_eating",
    "no_fastfood",
    "no_sweets",
    "no_chips",
    "no_sugary_drinks",
    "no_alcohol",
    "stop_when_full",
    "no_screen",
    "no_packaging",
    "water",
)

TRIGGER_CODES: tuple[str, ...] = (
    "stress",
    "tired",
    "boredom",
    "company",
    "hunger",
    "sleep",
    "no_food",
)

#: How far back a dated button may still write. Older messages are refused so a
#: forgotten checklist from weeks ago cannot rewrite history.
EDIT_WINDOW_DAYS = 7

MIN_WEIGHT_KG = 30.0
MAX_WEIGHT_KG = 300.0

#: Minimum filled days in each group before mood/energy are compared, so two
#: data points never pose as a pattern.
MIN_GROUP_FOR_COMPARISON = 3


class FoodValidationError(ValueError):
    pass


class FoodDayClosedError(ValueError):
    """The evening of that day was already submitted."""


class FoodDateError(ValueError):
    """The target date is in the future or outside the edit window."""


def _now() -> datetime:
    return datetime.now(UTC)


# --- Rule set --------------------------------------------------------------------
def active_rules(user: User) -> list[str]:
    """The user's enabled rules in catalog order."""
    if user.food_rules is None:
        return list(DEFAULT_RULES)
    chosen = {c for c in user.food_rules.split(",") if c}
    return [c for c in RULE_CODES if c in chosen]


def set_rule_enabled(session: Session, user: User, code: str, enabled: bool) -> list[str]:
    if code not in RULE_CODES:
        raise FoodValidationError(f"unknown rule: {code!r}")
    rules = set(active_rules(user))
    if enabled:
        rules.add(code)
    else:
        rules.discard(code)
    if not rules:
        raise FoodValidationError("at least one rule must stay enabled")
    user.food_rules = ",".join(c for c in RULE_CODES if c in rules)
    UserRepository(session).save(user)
    return active_rules(user)


def ordered_rules(user: User, focus: str | None) -> list[str]:
    """Active rules with the day's focus first (if it is still active)."""
    rules = active_rules(user)
    if focus in rules:
        rules.remove(focus)
        rules.insert(0, focus)
    return rules


# --- Dates -----------------------------------------------------------------------
def check_date(user: User, target_date: date, *, today: date | None = None) -> None:
    current = today or reflection_day(user.timezone)
    if target_date > current:
        raise FoodDateError("future date")
    if target_date < current - timedelta(days=EDIT_WINDOW_DAYS):
        raise FoodDateError("outside the edit window")


# --- Reads -----------------------------------------------------------------------
def get_day(session: Session, user: User, target_date: date) -> FoodDay | None:
    return FoodDayRepository(session).get(user.id, target_date)


def violations(day: FoodDay | None) -> list[str]:
    if day is None:
        return []
    return [r.rule_code for r in day.results if not r.kept]


def kept_rules(day: FoodDay | None) -> list[str]:
    if day is None:
        return []
    return [r.rule_code for r in day.results if r.kept]


def triggers_of(day: FoodDay | None) -> list[str]:
    if day is None or not day.triggers:
        return []
    chosen = set(day.triggers.split(","))
    return [c for c in TRIGGER_CODES if c in chosen]


def is_completed(day: FoodDay | None) -> bool:
    return day is not None and day.completed_at is not None


def focus_answered(day: FoodDay | None) -> bool:
    return day is not None and day.focus_set_at is not None


def has_completed_before(session: Session, user: User, target_date: date) -> bool:
    """Whether the user has ever submitted a food evening before ``target_date``
    — i.e. the feature is in use, so an unfilled day is worth a nudge."""
    start = target_date - timedelta(days=365)
    days = FoodDayRepository(session).list_in_range(user.id, start, target_date - timedelta(days=1))
    return any(is_completed(d) for d in days)


# --- Morning focus ---------------------------------------------------------------
def set_focus(session: Session, user: User, target_date: date, code: str | None) -> FoodDay:
    """``code=None`` records an explicit "no focus today"."""
    check_date(user, target_date)
    if code is not None and code not in active_rules(user):
        raise FoodValidationError(f"rule is not enabled: {code!r}")
    repo = FoodDayRepository(session)
    day = repo.get_or_create(user.id, target_date)
    if is_completed(day):
        raise FoodDayClosedError(target_date.isoformat())
    day.focus_rule = code
    day.focus_set_at = _now()
    return repo.save(day)


# --- Evening checklist -------------------------------------------------------------
def toggle_violation(session: Session, user: User, target_date: date, code: str) -> FoodDay:
    """Flip one rule between "kept" (the default, no row) and "broken"."""
    check_date(user, target_date)
    if code not in active_rules(user):
        raise FoodValidationError(f"rule is not enabled: {code!r}")
    repo = FoodDayRepository(session)
    day = repo.get_or_create(user.id, target_date)
    if is_completed(day):
        raise FoodDayClosedError(target_date.isoformat())
    if code in violations(day):
        repo.remove_result(day, code)
    else:
        repo.set_result(day, code, kept=False)
    return repo.save(day)


def submit_day(session: Session, user: User, target_date: date) -> FoodDay:
    """Close the evening: every active rule not marked broken is recorded as
    kept. Idempotent — a second tap on an already submitted day changes nothing.
    """
    check_date(user, target_date)
    repo = FoodDayRepository(session)
    day = repo.get_or_create(user.id, target_date)
    if is_completed(day):
        return day
    rules = active_rules(user)
    broken = set(violations(day))
    for code in [r.rule_code for r in day.results if r.rule_code not in rules]:
        repo.remove_result(day, code)
    for code in rules:
        repo.set_result(day, code, kept=code not in broken)
    if not broken:
        day.triggers = None
    day.completed_at = _now()
    return repo.save(day)


def toggle_trigger(session: Session, user: User, target_date: date, code: str) -> FoodDay:
    """Triggers are asked right after a submitted day with broken rules."""
    check_date(user, target_date)
    if code not in TRIGGER_CODES:
        raise FoodValidationError(f"unknown trigger: {code!r}")
    repo = FoodDayRepository(session)
    day = repo.get(user.id, target_date)
    if day is None or not is_completed(day) or not violations(day):
        raise FoodValidationError("triggers need a submitted day with a broken rule")
    chosen = set(triggers_of(day))
    chosen ^= {code}
    day.triggers = ",".join(c for c in TRIGGER_CODES if c in chosen) or None
    return repo.save(day)


def reopen_day(session: Session, user: User, target_date: date) -> FoodDay:
    """Explicit edit: the checklist becomes a draft again, keeping its marks."""
    check_date(user, target_date)
    repo = FoodDayRepository(session)
    day = repo.get_or_create(user.id, target_date)
    day.completed_at = None
    return repo.save(day)


# --- Weight --------------------------------------------------------------------
def parse_weight(text: str) -> float:
    raw = text.strip().lower().removesuffix("кг").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError as exc:
        raise FoodValidationError("not a number") from exc
    if not MIN_WEIGHT_KG <= value <= MAX_WEIGHT_KG:
        raise FoodValidationError("weight out of range")
    return round(value, 1)


def current_week(user: User) -> date:
    return week_start_date(reflection_day(user.timezone))


def save_weight(session: Session, user: User, value: float) -> WeightLog:
    today = reflection_day(user.timezone)
    return WeightLogRepository(session).upsert(
        user_id=user.id,
        week_start_date=week_start_date(today),
        logged_date=today,
        weight_kg=value,
    )


def get_weight(session: Session, user: User, week_start: date) -> WeightLog | None:
    return WeightLogRepository(session).get(user.id, week_start)


def previous_weight(logs: list[WeightLog], week_start: date) -> WeightLog | None:
    earlier = [w for w in logs if w.week_start_date < week_start]
    return earlier[-1] if earlier else None


# --- Statistics (pure) -------------------------------------------------------------
@dataclass(frozen=True)
class RuleRate:
    code: str
    kept: int
    total: int

    @property
    def percent(self) -> int:
        return round(self.kept / self.total * 100)


@dataclass(frozen=True)
class FoodStats:
    period_days: int
    completed_days: int
    clean_days: int
    streak: int
    rule_rates: list[RuleRate]  # worst first
    focus_kept: int
    focus_total: int
    triggers: list[tuple[str, int]]  # most frequent first
    energy_clean: float | None
    energy_broken: float | None
    mood_clean: float | None
    mood_broken: float | None
    last_weight: float | None
    weight_change_week: float | None
    weight_change_total: float | None


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def compute_streak(days: list[FoodDay], today: date) -> int:
    """Consecutive submitted days without a broken rule, ending today — or
    yesterday while today's evening is still open."""
    by_date = {d.food_date: d for d in days}
    cursor = today
    if not is_completed(by_date.get(today)):
        cursor -= timedelta(days=1)
    streak = 0
    while True:
        day = by_date.get(cursor)
        if not is_completed(day) or violations(day):
            return streak
        streak += 1
        cursor -= timedelta(days=1)


def compute_food_stats(
    days: list[FoodDay],
    entries: list[DailyEntry],
    weights: list[WeightLog],
    *,
    period_days: int,
    today: date,
) -> FoodStats:
    """``days`` must reach far enough back for the streak; only the trailing
    ``period_days`` window feeds the period figures."""
    if period_days <= 0:
        raise ValueError("period_days must be positive")
    window_start = today - timedelta(days=period_days - 1)
    done = [d for d in days if window_start <= d.food_date <= today and is_completed(d)]
    clean = [d for d in done if not violations(d)]

    counts: dict[str, list[int]] = {}
    for day in done:
        for result in day.results:
            kept, total = counts.setdefault(result.rule_code, [0, 0])
            counts[result.rule_code] = [kept + int(result.kept), total + 1]
    rates = [RuleRate(code, kept, total) for code, (kept, total) in counts.items() if total]
    rates.sort(key=lambda r: (r.kept / r.total, RULE_CODES.index(r.code) if r.code in RULE_CODES else 99))

    focus_days = [d for d in done if d.focus_rule]
    focus_kept = sum(1 for d in focus_days if d.focus_rule not in violations(d))

    trigger_counts: dict[str, int] = {}
    for day in done:
        for code in triggers_of(day):
            trigger_counts[code] = trigger_counts.get(code, 0) + 1
    triggers = sorted(trigger_counts.items(), key=lambda kv: (-kv[1], TRIGGER_CODES.index(kv[0])))

    entry_by_date = {e.entry_date: e for e in entries}
    clean_entries = [entry_by_date[d.food_date] for d in clean if d.food_date in entry_by_date]
    broken_entries = [
        entry_by_date[d.food_date] for d in done if violations(d) and d.food_date in entry_by_date
    ]
    compare = (
        len(clean_entries) >= MIN_GROUP_FOR_COMPARISON
        and len(broken_entries) >= MIN_GROUP_FOR_COMPARISON
    )

    ordered_weights = sorted(weights, key=lambda w: w.week_start_date)
    last = ordered_weights[-1] if ordered_weights else None
    prev = ordered_weights[-2] if len(ordered_weights) > 1 else None
    first = ordered_weights[0] if ordered_weights else None

    return FoodStats(
        period_days=period_days,
        completed_days=len(done),
        clean_days=len(clean),
        streak=compute_streak(days, today),
        rule_rates=rates,
        focus_kept=focus_kept,
        focus_total=len(focus_days),
        triggers=triggers,
        energy_clean=_mean([e.energy_score for e in clean_entries]) if compare else None,
        energy_broken=_mean([e.energy_score for e in broken_entries]) if compare else None,
        mood_clean=_mean([e.mood_score for e in clean_entries]) if compare else None,
        mood_broken=_mean([e.mood_score for e in broken_entries]) if compare else None,
        last_weight=last.weight_kg if last else None,
        weight_change_week=round(last.weight_kg - prev.weight_kg, 1) if last and prev else None,
        weight_change_total=(
            round(last.weight_kg - first.weight_kg, 1) if last and first and last is not first else None
        ),
    )
