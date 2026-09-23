"""CSV export (baseline sections 23-24, v1.1 unified contract, v1.2 outcomes).

One row = one calendar date, unioning that user's MorningIntent and DailyEntry
rows: morning-only, evening-only and both-date rows all appear, with empty
cells on the missing side. Outcomes are exported as the stored canonical
values (``done`` / ``partial`` / ``not_done``) rather than their emoji labels,
and a day closed before v1.2 simply has empty outcome cells. Produces UTF-8
(with BOM so Excel renders Russian text correctly) CSV using the standard
``csv`` module, which handles commas and newlines inside texts via proper
quoting. Exports contain only the requesting user's rows. Temporary files are
avoided entirely — the CSV is built in memory and sent as a Telegram upload, so
nothing accumulates on disk.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.database.models import DailyEntry, MorningIntent, User
from app.database.repositories import DailyEntryRepository, MorningIntentRepository

FIELDS = [
    "date",
    "morning_main_intention",
    "morning_main_outcome",
    "morning_secondary_intention",
    "morning_secondary_outcome",
    "morning_created_at",
    "day_score",
    "mood_score",
    "energy_score",
    "reflection_text",
    "evening_created_at",
    "evening_updated_at",
]

SCOPES = ("30d", "90d", "year", "all")


def _fmt_dt(value: date | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _scope_start(scope: str, today: date) -> date | None:
    if scope == "all":
        return None
    if scope == "year":
        return date(today.year, 1, 1)
    if scope == "90d":
        return today - timedelta(days=89)
    if scope == "30d":
        return today - timedelta(days=29)
    raise ValueError(f"unknown export scope: {scope}")


def build_csv(
    mornings: list[MorningIntent], entries: list[DailyEntry], *, scope: str, today: date
) -> bytes:
    start = _scope_start(scope, today)
    by_date_morning = {
        m.intention_date: m for m in mornings if start is None or m.intention_date >= start
    }
    by_date_entry = {
        e.entry_date: e for e in entries if start is None or e.entry_date >= start
    }
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(FIELDS)
    for day in sorted(set(by_date_morning) | set(by_date_entry)):
        if day > today:
            continue
        m = by_date_morning.get(day)
        e = by_date_entry.get(day)
        writer.writerow(
            [
                day.isoformat(),
                m.main_intention if m else "",
                (m.main_outcome or "") if m else "",
                (m.secondary_intention or "") if m else "",
                (m.secondary_outcome or "") if m else "",
                _fmt_dt(m.created_at) if m else "",
                e.day_score if e else "",
                e.mood_score if e else "",
                e.energy_score if e else "",
                e.reflection_text or "" if e else "",
                _fmt_dt(e.created_at) if e else "",
                _fmt_dt(e.updated_at) if e else "",
            ]
        )
    # utf-8-sig adds a BOM, which makes Excel and Google Sheets decode
    # Cyrillic text correctly while staying fully compatible with pandas.
    return buffer.getvalue().encode("utf-8-sig")


def export_user_csv(
    session: Session, user: User, *, scope: str, today: date
) -> tuple[str, bytes]:
    if scope not in SCOPES:
        raise ValueError(f"unknown export scope: {scope}")
    mornings = MorningIntentRepository(session).list_all(user.id)
    entries = DailyEntryRepository(session).list_all(user.id)
    filename = f"reflection_export_{today.isoformat()}.csv"
    return filename, build_csv(mornings, entries, scope=scope, today=today)
