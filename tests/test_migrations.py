"""Alembic migration tests (v1.1 spec 3 and 27-Migration; v1.2 spec 6).

Runs the real ``alembic`` CLI against throwaway SQLite files (subprocess so the
process-wide ``get_settings`` cache never leaks between scenarios). Verifies:

* a fresh DB upgrades to head with the new schema;
* an existing **v1-shaped** DB (a user + DailyEntry + weekly row, no morning
  columns) upgrades cleanly, keeps every existing row, and backfills
  ``users.morning_time = '08:30'``;
* an existing **v1.1.1-shaped** DB (users + entries + morning intentions +
  weekly rows, no outcome columns) upgrades to head with every row preserved
  and both outcomes NULL, and survives downgrade + re-upgrade;
* downgrade round-trips.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic(db_path: Path, *args: str) -> None:
    env = {"DATABASE_URL": f"sqlite:///{db_path}", "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**_clean_env(), **env},
        capture_output=True,
        text=True,
        check=False,  # the assertion below reports alembic's stderr
    )
    assert result.returncode == 0, f"alembic {args} failed:\n{result.stderr}"


def _clean_env() -> dict[str, str]:
    # Drop any inherited DATABASE_URL/.env influence; keep only what alembic
    # needs to import the app package.
    return {k: v for k, v in os.environ.items() if k in ("HOME", "VIRTUAL_ENV", "LANG")}


def _columns(db_path: Path, table: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _tables(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def test_fresh_upgrade_creates_schema(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    _alembic(db, "upgrade", "head")
    assert "morning_intents" in _tables(db)
    assert "morning_time" in _columns(db, "users")
    assert {"main_outcome", "secondary_outcome"} <= _columns(db, "morning_intents")
    # Unique(user_id, intention_date) present via an auto/index entry.
    con = sqlite3.connect(db)
    try:
        idx = [r[1] for r in con.execute("PRAGMA index_list('morning_intents')")]
        assert any("morning" in (i or "").lower() or "uq" in (i or "").lower() for i in idx)
    finally:
        con.close()


def test_upgrade_preserves_existing_v1_data(tmp_path: Path) -> None:
    db = tmp_path / "v1.db"
    # Build the v1 schema only (pre-morning revision).
    _alembic(db, "upgrade", "0001_initial")
    assert "morning_time" not in _columns(db, "users")
    assert "morning_intents" not in _tables(db)

    # Insert realistic v1 data with raw SQL (no morning_time column exists).
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO users (telegram_user_id, display_name, is_active, timezone, "
            "checkin_time, reminder_time, created_at, updated_at) "
            "VALUES (111, 'A', 1, 'Europe/Moscow', '21:30', '23:00', "
            "'2026-09-18 08:00:00', '2026-09-18 08:00:00')"
        )
        uid = con.execute("SELECT id FROM users WHERE telegram_user_id=111").fetchone()[0]
        con.execute(
            "INSERT INTO daily_entries (user_id, entry_date, day_score, mood_score, "
            "energy_score, reflection_text, questionnaire_version, created_at, updated_at) "
            "VALUES (?, '2026-09-18', 4, 3, 2, 'ok', 1, "
            "'2026-09-18 21:40:00', '2026-09-18 21:40:00')",
            (uid,),
        )
        con.execute(
            "INSERT INTO weekly_reflections (user_id, week_start_date, best_event, "
            "energy_drainer, want_more, created_at, updated_at) "
            "VALUES (?, '2026-09-14', 'b', 'e', 'w', "
            "'2026-09-18 22:00:00', '2026-09-18 22:00:00')",
            (uid,),
        )
        con.commit()
    finally:
        con.close()

    # Upgrade to head and confirm the old rows survived untouched.
    _alembic(db, "upgrade", "head")
    assert "morning_intents" in _tables(db)
    con = sqlite3.connect(db)
    try:
        user = con.execute(
            "SELECT morning_time, checkin_time, timezone FROM users WHERE telegram_user_id=111"
        ).fetchone()
        assert user == ("08:30", "21:30", "Europe/Moscow")  # backfilled + untouched
        entry = con.execute(
            "SELECT day_score, mood_score, energy_score, reflection_text FROM daily_entries"
        ).fetchone()
        assert entry == (4, 3, 2, "ok")
        assert con.execute("SELECT COUNT(*) FROM weekly_reflections").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM morning_intents").fetchone()[0] == 0
    finally:
        con.close()


def test_downgrade_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "rt.db"
    _alembic(db, "upgrade", "head")
    assert "morning_intents" in _tables(db)
    _alembic(db, "downgrade", "0001_initial")
    assert "morning_intents" not in _tables(db)
    assert "morning_time" not in _columns(db, "users")
    # Re-upgrade works after a downgrade.
    _alembic(db, "upgrade", "head")
    assert "morning_intents" in _tables(db)


# --------------------------------------------------------------------------
# v1.2: the production upgrade path runs over a real v1.1.1-shaped database
# --------------------------------------------------------------------------
_V111_ROWS = {
    "users": 2,
    "daily_entries": 2,
    "morning_intents": 2,
    "weekly_reflections": 1,
}


def _build_v111_db(db: Path) -> None:
    """Create the schema exactly as v1.1.1 shipped, then fill it with rows that
    look like real production data (no outcome columns exist yet)."""
    _alembic(db, "upgrade", "0002_morning_intents")
    cols = _columns(db, "morning_intents")
    assert {"main_outcome", "secondary_outcome"}.isdisjoint(cols)

    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            INSERT INTO users (telegram_user_id, display_name, is_active, timezone,
                               checkin_time, reminder_time, created_at, updated_at,
                               morning_time)
            VALUES (111, 'A', 1, 'Europe/Moscow', '21:30', '23:00',
                    '2026-09-20 08:00:00', '2026-09-21 21:35:00', '08:30'),
                   (222, 'B', 1, 'Europe/Berlin', '22:00', '23:30',
                    '2026-09-20 08:00:00', '2026-09-20 08:00:00', '09:00');

            INSERT INTO daily_entries (user_id, entry_date, day_score, mood_score,
                                       energy_score, reflection_text,
                                       questionnaire_version, created_at, updated_at)
            VALUES (1, '2026-09-19', 4, 3, 2, 'норм', 1,
                    '2026-09-19 21:40:00', '2026-09-19 21:40:00'),
                   (1, '2026-09-20', 5, 5, 5, NULL, 1,
                    '2026-09-20 21:40:00', '2026-09-20 21:40:00');

            INSERT INTO morning_intents (user_id, intention_date, main_intention,
                                         secondary_intention, created_at, updated_at)
            VALUES (1, '2026-09-19', 'релиз', 'зал, страховка',
                    '2026-09-19 08:31:00', '2026-09-19 08:32:00'),
                   (1, '2026-09-20', 'отчёт', NULL,
                    '2026-09-20 08:31:00', '2026-09-20 08:31:00');

            INSERT INTO weekly_reflections (user_id, week_start_date, best_event,
                                            energy_drainer, want_more,
                                            created_at, updated_at)
            VALUES (1, '2026-09-14', 'b', 'e', 'w',
                    '2026-09-20 22:00:00', '2026-09-20 22:00:00');
            """
        )
        con.commit()
    finally:
        con.close()


def _counts(db: Path) -> dict[str, int]:
    con = sqlite3.connect(db)
    try:
        return {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _V111_ROWS
        }
    finally:
        con.close()


def _intent_rows(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT user_id, intention_date, main_intention, secondary_intention, "
            "created_at, updated_at FROM morning_intents ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def test_v111_database_upgrades_without_losing_any_row(tmp_path: Path) -> None:
    db = tmp_path / "v111.db"
    _build_v111_db(db)
    before = _counts(db)
    intents_before = _intent_rows(db)
    assert before == _V111_ROWS

    _alembic(db, "upgrade", "head")

    cols = _columns(db, "morning_intents")
    assert {"main_outcome", "secondary_outcome"} <= cols
    # Every pre-existing row and column survived byte-for-byte.
    assert _counts(db) == before
    assert _intent_rows(db) == intents_before
    # No backfill: history never had an evening closure recorded.
    con = sqlite3.connect(db)
    try:
        outcomes = con.execute(
            "SELECT main_outcome, secondary_outcome FROM morning_intents"
        ).fetchall()
    finally:
        con.close()
    assert outcomes == [(None, None), (None, None)]


def test_v111_downgrade_and_reupgrade_keep_the_data(tmp_path: Path) -> None:
    db = tmp_path / "v111.db"
    _build_v111_db(db)
    intents_before = _intent_rows(db)
    _alembic(db, "upgrade", "head")

    # A user answers the closure before the rollback is exercised.
    con = sqlite3.connect(db)
    try:
        con.execute(
            "UPDATE morning_intents SET main_outcome='partial', "
            "secondary_outcome='done' WHERE intention_date='2026-09-19'"
        )
        con.commit()
    finally:
        con.close()

    _alembic(db, "downgrade", "0002_morning_intents")
    assert {"main_outcome", "secondary_outcome"}.isdisjoint(_columns(db, "morning_intents"))
    # Dropping the two new columns must not take the historical rows with them.
    assert _counts(db) == _V111_ROWS
    assert _intent_rows(db) == intents_before

    _alembic(db, "upgrade", "head")
    assert {"main_outcome", "secondary_outcome"} <= _columns(db, "morning_intents")
    assert _counts(db) == _V111_ROWS
    assert _intent_rows(db) == intents_before
    # The answers made after 0003 went away with the rolled-back columns; every
    # intention text is exactly where it was.
    con = sqlite3.connect(db)
    try:
        outcomes = dict(
            con.execute(
                "SELECT intention_date, main_outcome FROM morning_intents"
            ).fetchall()
        )
    finally:
        con.close()
    assert outcomes == {"2026-09-19": None, "2026-09-20": None}
