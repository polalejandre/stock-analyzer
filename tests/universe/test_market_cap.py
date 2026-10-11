from datetime import date

import pandas as pd
import pytest

from analyzer.config import UniverseRules
from analyzer.data.edgar.facts_read import latest_shares, latest_shares_dated
from analyzer.data.price_sync import replace_prices, replace_splits
from analyzer.store.db import connect, migrate
from analyzer.universe.market_cap import market_cap_on, months_before, split_factor_after
from analyzer.universe.members import Member, universe_on
from tests.helpers import bars

RULES = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=20, young_max_years=5,
                      young_min_market_cap=2e9)
D = date(2023, 6, 1)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def add_shares(conn, cik, value, period_end, filed, concept="shares_outstanding"):
    tag = ("dei:EntityCommonStockSharesOutstanding" if concept == "shares_outstanding"
           else "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding")
    conn.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (cik, concept, tag, "shares", "", period_end, value, filed, "10-Q",
                  f"a-{cik}-{filed}", "companyfacts"))


def newco(conn, close=15.0):
    """IPO 2022; stored (split-adjusted) close $15 every day, $75M traded a day."""
    replace_prices(conn, "NEWCO", bars(pd.bdate_range("2022-03-01", "2026-10-09"), close,
                                       volume=5_000_000.0), "yfinance")
    conn.execute("INSERT INTO young_listings VALUES ('NEWCO', 77, '2022-03-01', '2026-10-09')")


def test_months_before_clamps_the_day():
    assert months_before(date(2023, 6, 1), 15) == date(2022, 3, 1)
    assert months_before(date(2024, 5, 31), 15) == date(2023, 2, 28)
    assert months_before(date(2025, 5, 31), 15) == date(2024, 2, 29)
    assert months_before(date(2024, 1, 15), 1) == date(2023, 12, 15)


def test_latest_shares_dated_exposes_the_count_date_and_latest_shares_is_unchanged(conn):
    add_shares(conn, 77, 20e6, "2023-04-30", "2023-05-05")
    assert latest_shares_dated(conn, 77, D) == (20e6, date(2023, 4, 30))
    assert latest_shares(conn, 77, D) == 20e6
    assert latest_shares_dated(conn, 77, date(2023, 5, 4)) is None


def test_split_after_d_scales_shares_to_todays_basis(conn):
    """True close $150 x 20M shares = $3B; a 10:1 split in 2025 stores the close as $15."""
    newco(conn)
    add_shares(conn, 77, 20e6, "2023-04-30", "2023-05-05")
    replace_splits(conn, "NEWCO", [(date(2025, 6, 2), 10.0)])
    assert market_cap_on(conn, "NEWCO", 77, D) == pytest.approx(3e9)
    assert universe_on(conn, D, RULES) == [Member("NEWCO", 77, "young")]


def test_without_split_rows_market_cap_is_the_plain_product(conn):
    newco(conn)
    add_shares(conn, 77, 20e6, "2023-04-30", "2023-05-05")
    assert market_cap_on(conn, "NEWCO", 77, D) == pytest.approx(15.0 * 20e6)
    assert universe_on(conn, D, RULES) == []  # $300M


def test_split_before_the_share_count_date_adds_no_factor(conn):
    newco(conn)
    add_shares(conn, 77, 200e6, "2023-04-30", "2023-05-05")   # counted after the split
    replace_splits(conn, "NEWCO", [(date(2022, 9, 1), 10.0)])
    assert market_cap_on(conn, "NEWCO", 77, D) == pytest.approx(3e9)


def test_split_on_the_share_count_date_adds_no_factor(conn):
    newco(conn)
    add_shares(conn, 77, 200e6, "2023-04-30", "2023-05-05")
    replace_splits(conn, "NEWCO", [(date(2023, 4, 30), 10.0)])
    assert market_cap_on(conn, "NEWCO", 77, D) == pytest.approx(3e9)


def test_split_between_the_share_count_and_d_is_applied(conn):
    """Shares counted pre-split (20M); on d the stock already trades post-split at $15."""
    newco(conn)
    add_shares(conn, 77, 20e6, "2023-04-30", "2023-05-05")
    replace_splits(conn, "NEWCO", [(date(2023, 5, 15), 10.0)])
    assert market_cap_on(conn, "NEWCO", 77, D) == pytest.approx(3e9)


def test_every_later_split_multiplies_and_reverse_splits_divide(conn):
    newco(conn)
    add_shares(conn, 77, 20e6, "2023-04-30", "2023-05-05")
    replace_splits(conn, "NEWCO", [(date(2022, 1, 3), 7.0), (date(2024, 1, 2), 2.0),
                                   (date(2025, 1, 2), 10.0), (date(2026, 1, 2), 0.5)])
    assert split_factor_after(conn, "NEWCO", date(2023, 4, 30)) == pytest.approx(10.0)
    assert market_cap_on(conn, "NEWCO", 77, D) == pytest.approx(15.0 * 20e6 * 10.0)
    assert split_factor_after(conn, "OTHER", date(2023, 4, 30)) == 1.0


def test_stale_share_count_gives_no_market_cap(conn):
    newco(conn)
    add_shares(conn, 77, 1e9, "2022-02-28", "2022-03-10")    # 15 months + 1 day before d
    assert market_cap_on(conn, "NEWCO", 77, D) is None
    assert universe_on(conn, D, RULES) == []
    add_shares(conn, 77, 1e9, "2022-03-01", "2022-03-11")    # exactly 15 months: still usable
    assert market_cap_on(conn, "NEWCO", 77, D) == pytest.approx(15e9)


def test_no_price_or_no_shares_gives_no_market_cap(conn):
    add_shares(conn, 77, 20e6, "2023-04-30", "2023-05-05")
    assert market_cap_on(conn, "NEWCO", 77, D) is None
    newco(conn)
    assert market_cap_on(conn, "NEWCO", 78, D) is None
    assert market_cap_on(conn, "NEWCO", 77, date(2022, 2, 28)) is None   # before the first bar


def test_uses_the_close_on_or_before_d(conn):
    closes = [10.0, 10.0, 20.0]
    replace_prices(conn, "X", bars(["2023-05-30", "2023-05-31", "2023-06-02"], closes), "yfinance")
    add_shares(conn, 5, 1e6, "2023-04-30", "2023-05-05")
    assert market_cap_on(conn, "X", 5, D) == pytest.approx(10e6)


def test_merges_reach_the_share_lookup(conn):
    newco(conn)
    add_shares(conn, 300, 200e6, "2023-04-30", "2023-05-05")
    assert market_cap_on(conn, "NEWCO", 77, D) is None
    assert market_cap_on(conn, "NEWCO", 77, D, merges={77: [300]}) == pytest.approx(3e9)
