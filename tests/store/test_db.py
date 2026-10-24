import sqlite3

import pytest

from analyzer.store.db import connect, get_state, migrate, set_state, upsert_rows

TABLES = {
    "companies", "tickers", "index_snapshots", "index_membership", "young_listings", "prices",
    "facts", "filings", "insider_transactions", "macro", "provider_status", "fetch_failures",
    "sync_state", "splits", "ticker_aliases", "member_fates",
}


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert migrate(conn) == 3
    assert migrate(conn) == 0
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert conn.execute("SELECT version, name FROM schema_migrations").fetchall() == [
        (1, "001_initial.sql"), (2, "002_splits.sql"), (3, "003_identity.sql"),
    ]


def test_a_database_at_version_1_gets_the_splits_table(tmp_path):
    from analyzer.store.db import _migration_files

    conn = connect(tmp_path / "a.db")
    assert migrate(conn, files=_migration_files()[:1]) == 1
    conn.execute("INSERT INTO sync_state VALUES ('k', 'v')")
    conn.commit()
    assert migrate(conn) == 2
    assert get_state(conn, "k") == "v"
    conn.execute("INSERT INTO splits VALUES ('NVDA', '2024-06-10', 10.0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO splits VALUES ('NVDA', '2024-06-10', 4.0)")


def test_wal_mode(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connect_creates_parent_directory(tmp_path):
    connect(tmp_path / "nested" / "dir" / "a.db")
    assert (tmp_path / "nested" / "dir" / "a.db").exists()


def test_upsert_rows_replaces_on_primary_key(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    cols = ["series_id", "date", "value"]
    upsert_rows(conn, "macro", cols, [("NFCI", "2026-01-02", -0.5)])
    upsert_rows(conn, "macro", cols, [("NFCI", "2026-01-02", -0.4), ("NFCI", "2026-01-09", -0.3)])
    assert conn.execute("SELECT date, value FROM macro ORDER BY date").fetchall() == [
        ("2026-01-02", -0.4),
        ("2026-01-09", -0.3),
    ]


def test_sync_state_roundtrip(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    assert get_state(conn, "k") is None
    set_state(conn, "k", "v1")
    set_state(conn, "k", "v2")
    assert get_state(conn, "k") == "v2"


def test_failed_migration_rolls_back(tmp_path):
    conn = connect(tmp_path / "a.db")
    bad = [(1, "001_bad.sql", "CREATE TABLE a (x); CREATE TABLE a (x);")]
    with pytest.raises(sqlite3.Error):
        migrate(conn, files=bad)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='a'").fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=1").fetchone()[0] == 0
    assert conn.in_transaction is False


def test_identity_migration_adds_tables(tmp_path):
    from analyzer.store.db import connect, migrate
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(index_snapshots)")]
    assert "cik" in cols
    conn.execute("INSERT INTO ticker_aliases VALUES ('ANTM', 'ELV', 1156039, 'cik')")
    conn.execute("INSERT INTO member_fates VALUES ('ATVI', 'acquired', 'Microsoft acquired')")
