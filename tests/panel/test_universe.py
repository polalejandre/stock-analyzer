import pandas as pd

from analyzer.config import UniverseRules
from analyzer.panel.universe import liquidity_panel, load_entities, membership_panel, symbol_map
from analyzer.store.db import connect, migrate

RULES = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=3, young_max_years=5,
                      young_min_market_cap=2e9)


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.executemany("INSERT INTO index_membership VALUES (?, ?, ?, ?)", [
        ("sp500", "ANTM", "2015-01-01", "2022-07-01"), ("sp500", "ELV", "2022-07-01", None),
        ("sp500", "ATVI", "2015-01-01", "2023-10-13")])
    conn.execute("INSERT INTO ticker_aliases VALUES ('ANTM', 'ELV', 1156039, 'cik')")
    conn.execute("INSERT INTO member_fates VALUES ('ATVI', 'acquired', 'x')")
    conn.execute("INSERT INTO prices (symbol, date, close, source) VALUES ('ELV', '2015-01-02', 1, 't')")
    conn.execute("INSERT INTO tickers (ticker, cik, gics_sector, sector_etf, industry_etf, updated_at) "
                 "VALUES ('ELV', 1156039, 'Health Care', 'XLV', NULL, 'x')")
    conn.execute("INSERT INTO companies VALUES (1156039, 'Elevance', '6324', 'ins', 'x')")
    conn.execute("INSERT INTO index_snapshots (index_name, snapshot_date, revision_id, ticker, cik) "
                 "VALUES ('sp500', '2020-01-01', 1, 'ANTM', 1156039)")
    return conn


def test_symbol_map_resolves_aliases_and_drops_fated(tmp_path):
    assert symbol_map(_db(tmp_path)) == {"ANTM": "ELV", "ELV": "ELV"}


def test_membership_joins_old_and_new_ticker(tmp_path):
    conn = _db(tmp_path)
    dates = pd.DatetimeIndex(pd.to_datetime(["2020-01-02", "2022-06-30", "2022-07-01", "2026-01-02"]))
    m = membership_panel(conn, dates, symbol_map(conn))
    assert list(m.columns) == ["ELV"]
    assert m["ELV"].tolist() == [True, True, True, True]


def test_liquidity_needs_a_full_window():
    idx = pd.bdate_range("2024-01-01", periods=5)
    close = pd.DataFrame({"A": [10.0, 10, None, 10, 10]}, index=idx)
    volume = pd.DataFrame({"A": [3e6] * 5}, index=idx)
    ok = liquidity_panel(close, volume, RULES)
    assert ok["A"].tolist() == [False, False, False, False, False]  # the gap breaks every window
    close.iloc[2] = 10.0
    assert liquidity_panel(close, volume, RULES)["A"].tolist() == [False, False, True, True, True]


def test_entities_carry_cik_sector_and_reference_etf(tmp_path):
    e = load_entities(_db(tmp_path), ["ELV", "NOPE"])
    assert e.loc["ELV", "cik"] == 1156039
    assert e.loc["ELV", "industry"] == "sic3:632"
    assert e.loc["ELV", "ref_etf"] == "XLV"
    assert bool(e.loc["ELV", "identity_verified"]) is True    # verified through ANTM's snapshot
    assert pd.isna(e.loc["NOPE", "cik"]) and e.loc["NOPE", "ref_etf"] == "SPY"
