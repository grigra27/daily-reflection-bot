"""CSV export tests (baseline sections 23-24, 48 Export group; v1.1 union)."""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.database.models import DailyEntry, MorningIntent, User
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


def _add_intents(
    session: Session, user: User, items: dict[date, tuple[str, str | None]]
) -> None:
    for d, (main, secondary) in items.items():
        session.add(
            MorningIntent(
                user_id=user.id, intention_date=d,
                main_intention=main, secondary_intention=secondary,
            )
        )
    session.commit()


def _add_intent_with_outcomes(
    session: Session,
    user: User,
    d: date,
    *,
    main: str = "главное",
    secondary: str | None = "ещё",
    main_outcome: str | None = None,
    secondary_outcome: str | None = None,
) -> None:
    session.add(
        MorningIntent(
            user_id=user.id,
            intention_date=d,
            main_intention=main,
            secondary_intention=secondary,
            main_outcome=main_outcome,
            secondary_outcome=secondary_outcome,
        )
    )
    session.commit()


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_export_contains_only_current_user(session: Session) -> None:
    a = _make_user(session, 111)
    b = _make_user(session, 222)
    _add_entries(session, a, [date(2026, 9, 17), date(2026, 9, 18)])
    _add_entries(session, b, [date(2026, 9, 18)])
    _add_intent_with_outcomes(
        session, b, date(2026, 9, 18), main="B-plan", secondary=None, main_outcome="done"
    )

    filename, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    assert filename == "reflection_export_2026-09-18.csv"
    rows = _rows(data)
    assert len(rows) == 2
    assert all(r["day_score"] == "4" for r in rows)
    # User B's data (evening, morning and outcomes) must not appear even on
    # shared dates.
    assert {r["date"] for r in rows} == {"2026-09-17", "2026-09-18"}
    assert all(r["morning_main_intention"] == "" for r in rows)
    assert all(r["morning_main_outcome"] == "" for r in rows)


def test_csv_handles_cyrillic_and_special_chars(session: Session) -> None:
    a = _make_user(session, 111)
    tricky = "Много работы, но вечер был тёплым.\nДома хорошо."
    _add_entries(session, a, [date(2026, 9, 18)], reflection=tricky)
    _add_intents(session, a, {date(2026, 9, 18): ("зал, бассейн\nи чтото", None)})

    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    # BOM present for Excel compatibility.
    assert data.startswith(b"\xef\xbb\xbf")
    rows = _rows(data)
    assert len(rows) == 1
    assert rows[0]["reflection_text"] == tricky  # commas + newline survive round-trip
    assert rows[0]["morning_main_intention"] == "зал, бассейн\nи чтото"


def test_scope_filters_dates(session: Session) -> None:
    a = _make_user(session, 111)
    _add_entries(session, a, [date(2026, 1, 1), date(2026, 9, 18)])
    _, last30 = export_service.export_user_csv(session, a, scope="30d", today=TODAY)
    rows = _rows(last30)
    assert [r["date"] for r in rows] == ["2026-09-18"]


def test_scope_filters_morning_only_dates_by_calendar_date(session: Session) -> None:
    a = _make_user(session, 111)
    _add_intents(session, a, {date(2025, 12, 31): ("old", None), date(2026, 9, 18): ("new", "x")})
    _add_entries(session, a, [date(2026, 9, 18)])

    _, year = export_service.export_user_csv(session, a, scope="year", today=TODAY)
    rows = _rows(year)
    # 2025-12-31 morning is outside the current-year scope even though the
    # evening row of 2026-09-18 is inside.
    assert [r["date"] for r in rows] == ["2026-09-18"]

    _, all_time = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    assert [r["date"] for r in _rows(all_time)] == ["2025-12-31", "2026-09-18"]


def test_future_dates_are_not_exported(session: Session) -> None:
    a = _make_user(session, 111)
    _add_intents(session, a, {date(2026, 9, 19): ("завтра", None)})
    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    assert _rows(data) == []


def test_one_row_per_date_with_both_morning_and_evening(session: Session) -> None:
    a = _make_user(session, 111)
    _add_intents(session, a, {date(2026, 9, 18): ("главное", "ещё")})
    _add_entries(session, a, [date(2026, 9, 18)])

    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    rows = _rows(data)
    assert len(rows) == 1  # union of dates, not one row per source row
    row = rows[0]
    assert row["date"] == "2026-09-18"
    assert row["morning_main_intention"] == "главное"
    assert row["morning_secondary_intention"] == "ещё"
    assert row["morning_created_at"] != ""
    assert (row["day_score"], row["mood_score"], row["energy_score"]) == ("4", "3", "2")
    assert row["evening_created_at"] != "" and row["evening_updated_at"] != ""


def test_morning_only_and_evening_only_rows_are_unioned_and_sorted(session: Session) -> None:
    a = _make_user(session, 111)
    _add_intents(
        session, a,
        {
            date(2026, 9, 16): ("утро 16", None),
            date(2026, 9, 18): ("утро 18", "ещё 18"),  # both
        },
    )
    _add_entries(session, a, [date(2026, 9, 15), date(2026, 9, 18)])  # evening only + both

    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    rows = _rows(data)
    assert [r["date"] for r in rows] == [
        "2026-09-15", "2026-09-16", "2026-09-18",
    ]  # chronological union
    evening_only, morning_only, both = rows
    assert evening_only["morning_main_intention"] == ""
    assert evening_only["day_score"] == "4"
    assert morning_only["morning_main_intention"] == "утро 16"
    assert morning_only["day_score"] == "" and morning_only["reflection_text"] == ""
    assert morning_only["evening_created_at"] == ""
    assert both["morning_main_intention"] == "утро 18" and both["day_score"] == "4"


def test_missing_secondary_is_empty_string(session: Session) -> None:
    a = _make_user(session, 111)
    _add_intents(session, a, {date(2026, 9, 18): ("главное", None)})
    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    assert _rows(data)[0]["morning_secondary_intention"] == ""


def test_header_fields_match_v12_contract(session: Session) -> None:
    a = _make_user(session, 111)
    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    header = next(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert header == [
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


def test_outcome_columns_carry_canonical_values(session: Session) -> None:
    a = _make_user(session, 111)
    _add_intent_with_outcomes(
        session, a, date(2026, 9, 17),
        main="главное 17", secondary=None, main_outcome="partial",
    )
    _add_intent_with_outcomes(
        session, a, date(2026, 9, 18),
        main_outcome="done", secondary_outcome="not_done",
    )

    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    main_only, both = _rows(data)
    assert (main_only["morning_main_outcome"], main_only["morning_secondary_outcome"]) == (
        "partial", "",
    )
    # A missing secondary intention has no outcome either, even though the
    # row was written with one — the export never invents pairing.
    assert both["morning_main_outcome"] == "done"
    assert both["morning_secondary_outcome"] == "not_done"


def test_missing_outcomes_export_as_empty_not_null(session: Session) -> None:
    # Historical (pre-v1.2) rows: both outcome columns NULL.
    a = _make_user(session, 111)
    _add_intents(session, a, {date(2026, 9, 18): ("главное", "ещё")})
    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    row = _rows(data)[0]
    assert row["morning_main_outcome"] == ""
    assert row["morning_secondary_outcome"] == ""
    assert "None" not in data.decode("utf-8-sig")


def test_outcomes_export_without_an_evening_entry(session: Session) -> None:
    # Outcomes persist immediately, so a half-finished evening is a real state:
    # morning cells filled, day_score still blank.
    a = _make_user(session, 111)
    _add_intent_with_outcomes(
        session, a, date(2026, 9, 18), main_outcome="done", secondary_outcome="partial"
    )
    _, data = export_service.export_user_csv(session, a, scope="all", today=TODAY)
    row = _rows(data)[0]
    assert (row["morning_main_outcome"], row["morning_secondary_outcome"]) == ("done", "partial")
    assert row["day_score"] == "" and row["evening_created_at"] == ""


def test_unknown_scope_is_rejected(session: Session) -> None:
    a = _make_user(session, 111)
    with pytest.raises(ValueError):
        export_service.export_user_csv(session, a, scope="5d", today=TODAY)
