"""CSV export tests (baseline sections 23-24, 48 Export group)."""

from __future__ import annotations

import csv
import io
from datetime import date

from sqlalchemy.orm import Session

from app.database.models import DailyEntry, User
from app.services import export_service

TODAY = date(2026, 9, 18)


def _make_user(session: Session, tg_id: int) -> User:
    u = User(telegram_user_id=tg_id, timezone="Europe/Moscow")
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _add_entries(session: Session, user: User, dates: list[date], reflection: str | None = None) -> None:
    for d in dates:
        session.add(
            DailyEntry(
                user_id=user.id, entry_date=d, day_score=4, mood_score=3,
                energy_score=2, reflection_text=reflection,
            )
        )
    session.commit()


def test_export_contains_only_current_user(session: Session) -> None:
    a = _make_user(session, 111)
    b = _make_user(session, 222)
    _add_entries(session, a, [date(2026, 9, 17), date(2026, 9, 18)])
    _add_entries(session, b, [date(2026, 9, 18)])

    filename, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    assert filename == "reflection_export_2026-09-18.csv"
    text = data.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 2
    assert all(r["day_score"] == "4" for r in rows)
    # User B's data must not appear even though they share a date with A.
    assert {r["date"] for r in rows} == {"2026-09-17", "2026-09-18"}


def test_csv_handles_cyrillic_and_special_chars(session: Session) -> None:
    a = _make_user(session, 111)
    tricky = "Много работы, но вечер был тёплым.\nДома хорошо."
    _add_entries(session, a, [date(2026, 9, 18)], reflection=tricky)

    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    # BOM present for Excel compatibility.
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["reflection_text"] == tricky  # commas + newline survive round-trip


def test_scope_filters_dates(session: Session) -> None:
    a = _make_user(session, 111)
    _add_entries(session, a, [date(2026, 1, 1), date(2026, 9, 18)])
    _, last30 = export_service.export_user_csv(session, a, scope="30d", today=TODAY)
    rows = list(csv.DictReader(io.StringIO(last30.decode("utf-8-sig"))))
    assert [r["date"] for r in rows] == ["2026-09-18"]


def test_header_fields_match_baseline(session: Session) -> None:
    a = _make_user(session, 111)
    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    header = next(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert header == [
        "date", "day_score", "mood_score", "energy_score",
        "reflection_text", "created_at", "updated_at",
    ]
