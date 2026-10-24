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


def _two_company_ticker(conn):
    _member(conn, "COR", cik=1, snap="2013-01-01")
    _member(conn, "COR", cik=2, snap="2024-01-01")
    _priced(conn, "COR", cik=2)


def test_ticker_with_two_snapshot_companies_is_recycled(tmp_path):
    conn = _db(tmp_path)
    _two_company_ticker(conn)
    summary = build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT fate FROM member_fates WHERE ticker='COR'").fetchone() == ("recycled",)
    assert summary.recycled == 1 and summary.aliases == 0


def test_cik_merge_predecessor_is_the_same_company(tmp_path):
    conn = _db(tmp_path)
    _two_company_ticker(conn)
    merges = tmp_path / "cik_merges.toml"
    merges.write_text('[[merge]]\nticker = "COR"\npredecessor_ciks = [1]\n')
    build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml", merges_path=merges)
    assert conn.execute("SELECT COUNT(*) FROM member_fates").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM ticker_aliases").fetchone() == (0,)


def test_manual_alias_beats_recycled(tmp_path):
    conn = _db(tmp_path)
    _two_company_ticker(conn)
    _priced(conn, "NEW", cik=9)
    manual = tmp_path / "aliases.toml"
    manual.write_text('[[alias]]\nticker = "COR"\nprice_symbol = "NEW"\n')
    build_aliases(conn, reasons={}, manual_path=manual)
    assert conn.execute("SELECT COUNT(*) FROM member_fates").fetchone() == (0,)


def test_alias_stores_the_matching_cik(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "ANTM", cik=7, snap="2012-01-01")
    _member(conn, "ANTM", cik=1156039, snap="2015-01-01")
    _priced(conn, "ELV", cik=1156039)
    merges = tmp_path / "m.toml"
    merges.write_text('[[merge]]\nticker = "ELV"\npredecessor_ciks = [7]\n')
    build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml", merges_path=merges)
    assert conn.execute("SELECT price_symbol, cik FROM ticker_aliases").fetchone() == ("ELV", 1156039)


# --- backfill_snapshot_ciks / change_reasons ------------------------------------------------------

def _patch_revisions(monkeypatch, tables):
    import pandas as pd

    from analyzer.universe import aliases

    def fake_html(client, revid):
        v = tables[revid]
        if isinstance(v, Exception):
            raise v
        return v

    def fake_cons(html):
        return pd.DataFrame({"ticker": [t for t, _ in html], "cik": [c for _, c in html]},
                            dtype=object)

    def fake_changes(html):
        return pd.DataFrame({"removed_ticker": [t for t, _ in html], "reason": [r for _, r in html]})

    monkeypatch.setattr(aliases, "revision_html", fake_html)
    monkeypatch.setattr(aliases, "parse_constituents", fake_cons)
    monkeypatch.setattr(aliases, "parse_changes", fake_changes)


def _snap(conn, revid, ticker, index="sp500", snap="2020-01-01", cik=None):
    conn.execute("INSERT INTO index_snapshots (index_name, snapshot_date, revision_id, ticker, cik) "
                 "VALUES (?, ?, ?, ?, ?)", (index, snap, revid, ticker, cik))


def test_backfill_fills_skips_done_and_survives_unreadable(tmp_path, monkeypatch, caplog):
    import json

    from analyzer.store.db import get_state, set_state
    from analyzer.universe.aliases import PARSED_REVISIONS_KEY, backfill_snapshot_ciks
    conn = _db(tmp_path)
    for rev in (1, 2, 3):
        _snap(conn, rev, "AAA", snap=f"2020-0{rev}-01")
    _patch_revisions(monkeypatch, {1: [("AAA", 11)], 2: [("AAA", 22)], 3: RuntimeError("gone")})
    set_state(conn, PARSED_REVISIONS_KEY, json.dumps([2]))
    with caplog.at_level("WARNING"):
        assert backfill_snapshot_ciks(conn, client=None) == 1
    ciks = dict(conn.execute("SELECT revision_id, cik FROM index_snapshots"))
    assert ciks == {1: 11, 2: None, 3: None}
    assert "revision 3" in caplog.text
    assert json.loads(get_state(conn, PARSED_REVISIONS_KEY)) == [1, 2]


def test_change_reasons_reads_latest_revision_per_index(tmp_path, monkeypatch):
    from analyzer.universe.aliases import change_reasons
    conn = _db(tmp_path)
    _snap(conn, 1, "AAA", snap="2019-01-01")
    _snap(conn, 2, "AAA", snap="2020-01-01")
    _snap(conn, 3, "BBB", index="sp400", snap="2020-01-01")
    _patch_revisions(monkeypatch, {
        1: [("OLD", "stale reason")],
        2: [("X", "X acquired Y."), ("Z", "")],
        3: [("W", "Merger")],
    })
    assert change_reasons(conn, client=None) == {"X": "X acquired Y.", "W": "Merger"}
