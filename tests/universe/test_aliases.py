"""Aliases, fates and identity for past index members."""

from analyzer.store.db import connect, migrate
from analyzer.universe.aliases import build_aliases, classify_fate, snapshot_ciks


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    return conn


def _member(conn, ticker, cik=None, index="sp500", snap="2020-01-01"):
    conn.execute("INSERT OR IGNORE INTO index_membership VALUES (?, ?, '2015-01-01', NULL)",
                 (index, ticker))
    conn.execute("INSERT INTO index_snapshots (index_name, snapshot_date, revision_id, ticker, cik) "
                 "VALUES (?, ?, 1, ?, ?)", (index, snap, ticker, cik))


def _priced(conn, symbol, cik=None):
    conn.execute("INSERT INTO prices (symbol, date, close, source) VALUES (?, '2020-01-02', 1, 't')",
                 (symbol,))
    conn.execute("INSERT OR REPLACE INTO tickers (ticker, cik, updated_at) VALUES (?, ?, 'x')",
                 (symbol, cik))


def test_rename_is_aliased_through_the_snapshot_cik(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "ANTM", cik=1156039)
    _priced(conn, "ELV", cik=1156039)
    summary = build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT price_symbol, source FROM ticker_aliases WHERE ticker='ANTM'"
                        ).fetchone() == ("ELV", "cik")
    assert summary.aliases == 1


def test_manual_alias_wins(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "OLD", index="sp400")
    _priced(conn, "NEW", cik=5)
    manual = tmp_path / "aliases.toml"
    manual.write_text('[[alias]]\nticker = "OLD"\nprice_symbol = "NEW"\n')
    build_aliases(conn, reasons={}, manual_path=manual)
    assert conn.execute("SELECT price_symbol, source FROM ticker_aliases").fetchone() == ("NEW", "manual")


def test_delisted_member_gets_a_fate_from_the_reason(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "ATVI", cik=718877)
    summary = build_aliases(conn, reasons={"ATVI": "Microsoft acquired Activision Blizzard."},
                            manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT fate FROM member_fates WHERE ticker='ATVI'").fetchone() == ("acquired",)
    assert summary.excluded == {"acquired": 1}


def test_recycled_ticker_is_excluded(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "COR", cik=1490892)        # CoreSite in the snapshot
    _priced(conn, "COR", cik=1140859)        # today's COR is Cencora
    summary = build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT fate FROM member_fates WHERE ticker='COR'").fetchone() == ("recycled",)
    assert summary.recycled == 1


def test_same_company_ticker_is_kept(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "AAPL", cik=320193)
    _priced(conn, "AAPL", cik=320193)
    build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT COUNT(*) FROM member_fates").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM ticker_aliases").fetchone() == (0,)


def test_classify_fate():
    assert classify_fate("XYZ Corp. acquired ABC.") == "acquired"
    assert classify_fate("Merger with DEF") == "acquired"
    assert classify_fate("Market capitalization change.") == "other"
    assert classify_fate(None) == "unknown"


def test_snapshot_ciks_collects_every_cik(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "COR", cik=1, snap="2015-01-01")
    _member(conn, "COR", cik=2, snap="2024-01-01")
    assert snapshot_ciks(conn) == {"COR": {1, 2}}
