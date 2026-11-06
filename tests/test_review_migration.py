import sqlite3

from tests.test_migrations import _alembic, _build_v111_db


def test_cache_migration_preserves_existing_data_and_is_reversible(tmp_path):
    db = tmp_path / "upgrade.db"
    _build_v111_db(db)
    _alembic(db, "upgrade", "0007_vitamin_intakes")
    with sqlite3.connect(db) as con:
        tables = [
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if r[0] != "alembic_version"
        ]
        before = {t: con.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
    _alembic(db, "upgrade", "head")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0008_weekly_analyses",
        )
        assert con.execute("SELECT count(*) FROM weekly_analyses").fetchone() == (0,)
        assert {t: con.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables} == before
    _alembic(db, "downgrade", "0007_vitamin_intakes")
    with sqlite3.connect(db) as con:
        assert {t: con.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables} == before
    _alembic(db, "upgrade", "head")
