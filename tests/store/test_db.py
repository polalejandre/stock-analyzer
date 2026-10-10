from analyzer.store.db import connect, get_state, migrate, set_state, upsert_rows

TABLES = {
    "companies", "tickers", "index_snapshots", "index_membership", "young_listings", "prices",
    "facts", "filings", "insider_transactions", "macro", "provider_status", "fetch_failures",
    "sync_state",
}


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert migrate(conn) == 1
    assert migrate(conn) == 0
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


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
