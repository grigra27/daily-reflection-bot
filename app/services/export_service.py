"""CSV export (baseline sections 23-24).

Produces UTF-8 (with BOM so Excel renders Russian text correctly) CSV using the
standard ``csv`` module, which handles commas and newlines inside reflection
text via proper quoting. Exports contain only the requesting user's rows.
Temporary files are avoided entirely — the CSV is built in memory and sent as a
Telegram upload, so nothing accumulates on disk.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.database.models import DailyEntry, User
from app.database.repositories import DailyEntryRepository

FIELDS = [
    "date",
    "day_score",
    "mood_score",
    "energy_score",
    "reflection_text",
    "created_at",
    "updated_at",
]

SCOPES = ("30d", "90d", "year", "all")


def _fmt_dt(value: date | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def select_entries(entries: list[DailyEntry], *, scope: str, today: date) -> list[DailyEntry]:
    if scope == "all":
        return entries
    if scope == "year":
        start = date(today.year, 1, 1)
    elif scope == "90d":
        start = today - timedelta(days=89)
    elif scope == "30d":
        start = today - timedelta(days=29)
    else:
        raise ValueError(f"unknown export scope: {scope}")
    return [e for e in entries if e.entry_date >= start and e.entry_date <= today]


def build_csv(entries: list[DailyEntry]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(FIELDS)
    for e in sorted(entries, key=lambda row: row.entry_date):
        writer.writerow(
            [
                e.entry_date.isoformat(),
                e.day_score,
                e.mood_score,
                e.energy_score,
                e.reflection_text or "",
                _fmt_dt(e.created_at),
                _fmt_dt(e.updated_at),
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
    all_entries = DailyEntryRepository(session).list_all(user.id)
    selected = select_entries(all_entries, scope=scope, today=today)
    filename = f"reflection_export_{today.isoformat()}.csv"
    return filename, build_csv(selected)
