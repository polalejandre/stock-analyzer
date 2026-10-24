import numpy as np
import pandas as pd

from analyzer.config import PROJECT_ROOT, load_rules
from analyzer.features.regime import (
    MAX_AGE_DAYS,
    PUBLICATION_LAG_DAYS,
    _fresh,
    load_macro,
    regime,
)
from analyzer.store.db import connect, migrate
from tests.panel_fixtures import assert_truncation_invariant

RULES = load_rules(PROJECT_ROOT / "rules.toml").regime


def test_value_is_usable_only_after_its_publication_lag(tmp_path):
    # M2SL dated 2024-02-01 is usable from 2024-02-01 + 58 days = Saturday 2024-03-30,
    # so the first trading day it can be used is Monday 2024-04-01.
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.execute("INSERT INTO macro VALUES ('M2SL', '2024-02-01', 100.0)")
    dates = pd.bdate_range("2024-02-01", "2024-04-30")
    m = load_macro(conn, dates)
    available = pd.Timestamp("2024-02-01") + pd.Timedelta(days=PUBLICATION_LAG_DAYS["M2SL"])
    assert available == pd.Timestamp("2024-03-30")
    assert np.isnan(m.loc["2024-03-29", "M2SL"])          # the day before date + lag
    assert m.loc["2024-04-01", "M2SL"] == 100.0           # first trading day on or after it
    assert m["M2SL"].first_valid_index() == pd.Timestamp("2024-04-01")


def _macro(n, **cols):
    idx = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({k: v for k, v in cols.items()}, index=idx)


def test_regime_counts_loose_indicators():
    n = 900
    rising = np.linspace(100, 200, n)
    falling = np.linspace(200, 100, n)
    loose = _macro(n, NFCI=-0.5, BAMLH0A0HYM2=falling, WALCL=rising, M2SL=rising,
                   RRPONTSYD=falling, DFII10=falling)
    assert regime(loose, RULES).iloc[-1] == "loose"
    tight = _macro(n, NFCI=0.5, BAMLH0A0HYM2=rising, WALCL=falling, M2SL=falling,
                   RRPONTSYD=rising, DFII10=rising)
    assert regime(tight, RULES).iloc[-1] == "tight"
    assert regime(loose, RULES).iloc[0] == "unknown"           # no 3-year median yet


def test_exactly_three_loose_indicators_is_neutral():
    n = 900
    rising = np.linspace(100, 200, n)
    falling = np.linspace(200, 100, n)
    # loose: NFCI, WALCL, RRPONTSYD; tight: the other three
    mixed = _macro(n, NFCI=-0.5, BAMLH0A0HYM2=rising, WALCL=rising, M2SL=falling,
                   RRPONTSYD=falling, DFII10=rising)
    assert regime(mixed, RULES).iloc[-1] == "neutral"


def _five_known(loose_count):
    """NFCI missing; the first `loose_count` of the other five indicators loose."""
    n = 900
    rising, falling = np.linspace(100, 200, n), np.linspace(200, 100, n)
    loose = {"BAMLH0A0HYM2": falling, "WALCL": rising, "M2SL": rising, "RRPONTSYD": falling,
             "DFII10": falling}
    tight = {"BAMLH0A0HYM2": rising, "WALCL": falling, "M2SL": falling, "RRPONTSYD": rising,
             "DFII10": rising}
    cols = {k: (loose if i < loose_count else tight)[k] for i, k in enumerate(loose)}
    return _macro(n, NFCI=np.nan, **cols)


def test_one_missing_series_classifies_from_the_other_five():
    # five known: loose >= ceil(4/6 x 5) = 4, tight <= floor(2/6 x 5) = 1
    got = [regime(_five_known(c), RULES).iloc[-1] for c in range(6)]
    assert got == ["tight", "tight", "neutral", "neutral", "loose", "loose"]


def test_two_missing_series_make_the_regime_unknown():
    frame = _five_known(5).assign(DFII10=np.nan)
    assert regime(frame, RULES).iloc[-1] == "unknown"
    assert RULES.min_known == 5


def test_series_with_no_rows_is_left_out(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    dates = pd.bdate_range("2015-01-01", periods=900)
    for series in PUBLICATION_LAG_DAYS:
        if series in ("DFII10", "NFCI"):
            continue                                      # no rows at all for these series
        conn.executemany("INSERT INTO macro VALUES (?, ?, ?)",
                         [(series, d.strftime("%Y-%m-%d"), 100.0 + i)
                          for i, d in enumerate(dates)])
    m = load_macro(conn, dates)
    assert m["DFII10"].isna().all() and m["DFII10_age"].isna().all()
    assert regime(m, RULES).iloc[-1] == "unknown"          # four known
    conn.executemany("INSERT INTO macro VALUES (?, ?, ?)",
                     [("NFCI", d.strftime("%Y-%m-%d"), -1.0) for d in dates])
    # five known; loose: NFCI, WALCL, M2SL (rising); tight: HY, RRP (rising) -> 3 of 5: neutral
    assert regime(load_macro(conn, dates), RULES).iloc[-1] == "neutral"


def test_a_stale_value_is_unknown_after_three_cadences(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.execute("INSERT INTO macro VALUES ('DFII10', '2024-01-02', 1.5)")      # known 01-03
    conn.execute("INSERT INTO macro VALUES ('WALCL', '2024-01-03', 7.0)")       # known 01-10
    dates = pd.bdate_range("2024-01-01", "2024-02-15")
    m = load_macro(conn, dates)
    assert m.loc["2024-01-08", "DFII10_age"] == 5 and m.loc["2024-01-09", "DFII10_age"] == 6
    assert m.loc["2024-01-31", "WALCL_age"] == 21 and m.loc["2024-02-01", "WALCL_age"] == 22
    assert m.loc["2024-02-15", "DFII10"] == 1.5                # the value is still carried
    assert _fresh(m, "DFII10").loc["2024-01-08"] and not _fresh(m, "DFII10").loc["2024-01-09"]
    assert _fresh(m, "WALCL").loc["2024-01-31"] and not _fresh(m, "WALCL").loc["2024-02-01"]
    assert MAX_AGE_DAYS == {"WALCL": 21, "RRPONTSYD": 5, "M2SL": 93, "NFCI": 21,
                            "BAMLH0A0HYM2": 5, "DFII10": 5}


def test_stale_series_drops_out_of_the_count():
    frame = _five_known(5).assign(NFCI=-0.5)                  # six known, six loose
    assert regime(frame, RULES).iloc[-1] == "loose"
    stale = frame.assign(WALCL_age=30.0, M2SL_age=100.0)       # two stale: four known
    assert regime(stale, RULES).iloc[-1] == "unknown"
    one = frame.assign(WALCL_age=30.0)                         # five known, all loose
    assert regime(one, RULES).iloc[-1] == "loose"


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
        return regime(load_macro(conn, cut_dates), RULES)

    assert_truncation_invariant(compute, [dates[800], dates[899]])
