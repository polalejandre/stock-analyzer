from datetime import date

import pandas as pd

from analyzer.panel.prices import load_price_panels, trading_dates
from analyzer.store.db import connect, migrate


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    rows = [("SPY", "2024-01-02", 10, 11, 9, 10, 10, 100), ("SPY", "2024-01-03", 10, 11, 9, 10.5, 10.5, 100),
            ("SPY", "2024-01-04", 10, 11, 9, 11, 11, 100),
            ("AAA", "2024-01-02", 20, 21, 19, 20, 19, 5), ("AAA", "2024-01-04", 20, 21, 19, 22, None, 5)]
    conn.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,'t')", rows)
    return conn


def test_trading_dates_follow_spy(tmp_path):
    dates = trading_dates(_db(tmp_path), date(2024, 1, 1), date(2024, 1, 31))
    assert list(dates.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_panels_align_and_fill_missing_adjusted_close(tmp_path):
    conn = _db(tmp_path)
    dates = trading_dates(conn, date(2024, 1, 1), date(2024, 1, 31))
    p = load_price_panels(conn, ["AAA", "ZZZ"], dates)
    assert list(p.close.columns) == ["AAA", "ZZZ"]
    assert pd.isna(p.close.loc["2024-01-03", "AAA"])          # no bar that day
    assert p.adj_close.loc["2024-01-04", "AAA"] == 22          # NULL adj_close -> close
    assert p.close["ZZZ"].isna().all()                         # unknown symbol: all NaN
    assert p.adj_open.loc["2024-01-02", "AAA"] == 20 * 19 / 20


def test_upto_cuts_every_field(tmp_path):
    conn = _db(tmp_path)
    p = load_price_panels(conn, ["AAA"], trading_dates(conn, date(2024, 1, 1), date(2024, 1, 31)))
    q = p.upto("2024-01-03")
    assert q.volume.index[-1] == pd.Timestamp("2024-01-03")
    assert len(q.open) == 2
