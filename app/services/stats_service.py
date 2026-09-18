"""Statistics calculation (baseline sections 20-22).

Pure function over a list of DailyEntry rows so it can be unit-tested without
Telegram or a live session. Key rules:

* averages are computed only over filled days;
* skipped days are NOT counted as 0 and are excluded from averages;
* ``completion_rate = completed_days / days_in_period``;
* ``good_day_ratio = count(day_score >= 4) / completed_days``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.database.models import DailyEntry


@dataclass(frozen=True)
class StatsResult:
    period_days: int
    completed_days: int
    avg_day: float | None
    avg_mood: float | None
    avg_energy: float | None
    distribution: dict[int, int]  # day_score -> count (keys 1..5)
    good_and_excellent: int
    good_day_ratio: float | None  # percentage 0..100
    completion_rate: float  # 0..1
    recent7_avg: float | None
    previous7_avg: float | None


def _mean(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def compute_stats(entries: list[DailyEntry], *, period_days: int, today: date) -> StatsResult:
    if period_days <= 0:
        raise ValueError("period_days must be positive")

    # Restrict to the trailing calendar window ending today (inclusive).
    from datetime import timedelta

    window_start = today - timedelta(days=period_days - 1)
    in_window = [e for e in entries if window_start <= e.entry_date <= today]

    completed_days = len(in_window)
    avg_day = _mean([e.day_score for e in in_window])
    avg_mood = _mean([e.mood_score for e in in_window])
    avg_energy = _mean([e.energy_score for e in in_window])

    distribution = {score: 0 for score in range(5, 0, -1)}
    for entry in in_window:
        distribution[entry.day_score] = distribution.get(entry.day_score, 0) + 1

    good_and_excellent = sum(1 for e in in_window if e.day_score >= 4)
    good_ratio = round(good_and_excellent / completed_days * 100) if completed_days else None
    completion_rate = completed_days / period_days

    # Dynamics over completed entries only, most-recent first. "Last 7 filled
    # days" vs "previous 7 filled days"; shown whenever data exists, without
    # any psychological interpretation.
    ordered = sorted(in_window, key=lambda e: e.entry_date, reverse=True)
    recent = ordered[:7]
    previous = ordered[7:14]
    recent7_avg = _mean([e.day_score for e in recent])
    previous7_avg = _mean([e.day_score for e in previous])

    return StatsResult(
        period_days=period_days,
        completed_days=completed_days,
        avg_day=avg_day,
        avg_mood=avg_mood,
        avg_energy=avg_energy,
        distribution=distribution,
        good_and_excellent=good_and_excellent,
        good_day_ratio=good_ratio,
        completion_rate=completion_rate,
        recent7_avg=recent7_avg,
        previous7_avg=previous7_avg,
    )
