"""Statistics calculation tests (baseline sections 20-22, 48).

compute_stats is a pure function over DailyEntry rows, so no database or
Telegram is involved here.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.database.models import DailyEntry
from app.services import stats_service

TODAY = date(2026, 9, 18)


def _entry(days_ago: int, day: int, mood: int, energy: int) -> DailyEntry:
    return DailyEntry(
        entry_date=TODAY - timedelta(days=days_ago),
        day_score=day,
        mood_score=mood,
        energy_score=energy,
    )


def test_empty_period_is_safe() -> None:
    stats = stats_service.compute_stats([], period_days=30, today=TODAY)
    assert stats.completed_days == 0
    assert stats.avg_day is None
    assert stats.good_day_ratio is None
    assert stats.completion_rate == 0.0


def test_single_entry() -> None:
    stats = stats_service.compute_stats([_entry(0, 4, 3, 2)], period_days=30, today=TODAY)
    assert stats.completed_days == 1
    assert stats.avg_day == 4.0
    assert stats.good_day_ratio == 100
    assert round(stats.completion_rate, 3) == round(1 / 30, 3)


def test_averages_ignore_missing_days() -> None:
    # Three filled days; the other 27 days are skipped and must not count as 0.
    entries = [_entry(0, 5, 5, 5), _entry(1, 3, 3, 3), _entry(2, 4, 4, 4)]
    stats = stats_service.compute_stats(entries, period_days=30, today=TODAY)
    assert stats.completed_days == 3
    assert stats.avg_day == round((5 + 3 + 4) / 3, 1)
    assert stats.completion_rate == round(3 / 30, 4) or stats.completion_rate == 3 / 30


def test_good_day_ratio_uses_completed_denominator() -> None:
    entries = [_entry(0, 5, 5, 5), _entry(1, 4, 4, 4), _entry(2, 2, 2, 2), _entry(3, 1, 1, 1)]
    stats = stats_service.compute_stats(entries, period_days=10, today=TODAY)
    # 2 of 4 completed days are >=4
    assert stats.good_and_excellent == 2
    assert stats.good_day_ratio == 50
    assert stats.completed_days == 4


def test_days_outside_window_excluded() -> None:
    entries = [_entry(0, 5, 5, 5), _entry(40, 1, 1, 1)]  # 40 days ago -> outside 30-day window
    stats = stats_service.compute_stats(entries, period_days=30, today=TODAY)
    assert stats.completed_days == 1
    assert stats.avg_day == 5.0


def test_distribution_counts() -> None:
    entries = [_entry(0, 5, 5, 5), _entry(1, 5, 5, 5), _entry(2, 3, 3, 3)]
    stats = stats_service.compute_stats(entries, period_days=7, today=TODAY)
    assert stats.distribution[5] == 2
    assert stats.distribution[3] == 1
    assert stats.distribution[1] == 0
