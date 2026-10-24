import numpy as np
import pandas as pd

from analyzer.features.regime import PUBLICATION_LAG_DAYS, load_macro, regime
from analyzer.store.db import connect, migrate
from tests.panel_fixtures import assert_truncation_invariant


def test_value_is_usable_only_after_its_publication_lag(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.execute("INSERT INTO macro VALUES ('M2SL', '2024-01-01', 100.0)")
    dates = pd.bdate_range("2024-01-01", "2024-03-29")
    m = load_macro(conn, dates)
    first = m["M2SL"].first_valid_index()
    assert first >= pd.Timestamp("2024-01-01") + pd.Timedelta(days=PUBLICATION_LAG_DAYS["M2SL"])


def _macro(n, **cols):
    idx = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({k: v for k, v in cols.items()}, index=idx)


def test_regime_counts_loose_indicators():
    n = 900
    rising = np.linspace(100, 200, n)
    falling = np.linspace(200, 100, n)
    loose = _macro(n, NFCI=-0.5, BAMLH0A0HYM2=falling, WALCL=rising, M2SL=rising,
                   RRPONTSYD=falling, DFII10=falling)
    assert regime(loose).iloc[-1] == "loose"
    tight = _macro(n, NFCI=0.5, BAMLH0A0HYM2=rising, WALCL=falling, M2SL=falling,
                   RRPONTSYD=rising, DFII10=rising)
    assert regime(tight).iloc[-1] == "tight"
    assert regime(loose).iloc[0] == "unknown"           # no 3-year median yet


def test_regime_is_truncation_invariant(tmp_path):
    n = 900
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2015-01-01", periods=n)
    series = {s: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
              for s in PUBLICATION_LAG_DAYS}
    rows = [(s, d.strftime("%Y-%m-%d"), float(v))
            for s, vals in series.items() for d, v in zip(dates, vals, strict=True)]

    def compute(end):
        # Cut the raw macro rows and the trading dates before computing anything.
        cut_rows = rows if end is None else [r for r in rows if r[1] <= end.strftime("%Y-%m-%d")]
        conn = connect(tmp_path / f"{end.date() if end is not None else 'full'}.db")
        migrate(conn)
        conn.executemany("INSERT INTO macro VALUES (?, ?, ?)", cut_rows)
        cut_dates = dates if end is None else dates[dates <= end]
        return regime(load_macro(conn, cut_dates))

    assert_truncation_invariant(compute, [dates[800], dates[899]])
