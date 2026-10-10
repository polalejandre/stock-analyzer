"""Opt-in checks against the real sources: uv run pytest -m live -v"""

import io
import re
import zipfile
from datetime import date, timedelta

import pandas as pd
import pytest

from analyzer.config import ConfigError, load_settings
from analyzer.data.clients import make_clients
from analyzer.data.edgar.companies import fetch_submissions, load_ticker_map
from analyzer.data.edgar.companyfacts import extract_facts, fetch_companyfacts, store_facts
from analyzer.data.edgar.daily_index import fetch_master_index
from analyzer.data.edgar.facts_read import latest_shares, load_concept
from analyzer.data.edgar.insider_bulk import _fetch_zip, parse_insider_zip
from analyzer.data.edgar.instance import fetch_instance_facts
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.edgar.quarters import discrete_quarters
from analyzer.data.fred import fetch_series
from analyzer.data.prices import NasdaqPrices, YFinancePrices
from analyzer.store.db import connect, migrate
from analyzer.universe.wikipedia import snapshot_for

pytestmark = pytest.mark.live
TODAY = date.today()


@pytest.fixture(scope="module")
def settings():
    try:
        return load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def clients(settings):
    return make_clients(settings)


@pytest.fixture(scope="module")
def ticker_map(clients):
    return load_ticker_map(clients.sec)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "live.db")
    migrate(c)
    return c


def cik(ticker_map, ticker):
    return int(ticker_map.set_index("ticker").at[ticker, "cik"])


def facts_for(conn, clients, ticker_map, ticker, merges=None):
    c = cik(ticker_map, ticker)
    for k in [c, *(merges or {}).get(c, [])]:
        cf = fetch_companyfacts(clients.sec, k)
        if cf is not None:
            store_facts(conn, extract_facts(cf, k))
    return c


def test_yfinance_and_nasdaq(clients):
    start = TODAY - timedelta(days=14)
    assert "AAPL" in YFinancePrices().fetch(["AAPL"], start, TODAY)
    out = NasdaqPrices(clients.nasdaq).fetch(["BRK-B", "SMH"], start, TODAY)
    assert set(out) == {"BRK-B", "SMH"}


def test_wikipedia_current_and_2014(clients):
    for index_name in ("sp500", "sp400"):
        _, now = snapshot_for(clients.wiki, index_name, TODAY + timedelta(days=1), TODAY)
        _, then = snapshot_for(clients.wiki, index_name, date(2014, 1, 1), TODAY)
        assert now["gics_sector"].notna().mean() > 0.95
        assert len(then) > 350


def test_ticker_map_and_submissions(clients, ticker_map):
    subs = fetch_submissions(clients.sec, cik(ticker_map, "AAPL"))
    assert subs.sic == "3571"
    assert (subs.filings.form == "10-Q").sum() >= 30  # older pages were read


def test_nvda_capex_is_continuous(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "NVDA")
    q = discrete_quarters(load_concept(conn, c, "capex"))
    q = q[q.period_end >= "2015-01-01"]
    gaps = q.period_end.diff().dt.days.dropna()
    assert len(q) >= 40 and gaps.max() <= 120


def test_xom_history_survives_cik_change(conn, clients, ticker_map):
    merges = load_cik_merges(ticker_map)
    c = facts_for(conn, clients, ticker_map, "XOM", merges)
    print(f"\nXOM current CIK: {c}; merges entry: {merges.get(c)} "
          "(None means the merge is a no-op: the current CIK already carries the history)")
    revenue = discrete_quarters(load_concept(conn, c, "revenue", merges=merges))
    # If this fails, look up XOM's predecessor CIK on EDGAR and fix cik_merges.toml.
    assert revenue.period_end.min() <= pd.Timestamp("2012-12-31")


def test_cost_sixteen_week_quarter(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "COST")
    q = discrete_quarters(load_concept(conn, c, "revenue"))
    assert ((q.days >= 105) & (q.days <= 120)).any()


def test_bank_has_net_income_but_no_operating_income(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "JPM")
    assert not load_concept(conn, c, "net_income").empty
    recent = load_concept(conn, c, "operating_income")
    assert recent.empty or recent.period_end.max() < pd.Timestamp(TODAY - timedelta(days=365))


def test_googl_dual_class_shares(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "GOOGL")
    shares = latest_shares(conn, c, TODAY)
    stored = conn.execute(
        "SELECT concept, tag, period_end, value, filed, form FROM facts "
        "WHERE cik = ? AND concept = 'shares_outstanding' AND period_end = "
        "(SELECT MAX(period_end) FROM facts WHERE cik = ? AND concept = 'shares_outstanding') "
        "ORDER BY filed", (c, c)).fetchall()
    print(f"\nGOOGL latest_shares={shares}; stored rows for latest period_end: {stored}")
    # Alphabet Class A+B+C is about 12.1B. Under 7e9 is one class (the summing rule is not
    # engaging); over 20e9 double-counts.
    assert stored, "no shares_outstanding rows"
    assert shares == float(stored[-1][3]), (
        f"latest_shares={shares} is not the latest stored shares_outstanding row: {stored}")
    assert shares is not None and 11e9 <= shares <= 13.5e9, (
        f"latest_shares={shares}; stored shares_outstanding rows: {stored}")


def test_brk_b_class_shares(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "BRK-B")
    shares = latest_shares(conn, c, TODAY)
    # Informational: Berkshire reports Class A and B in very different counts.
    print(f"\nBRK-B (CIK {c}) latest_shares={shares}")
    assert shares is not None and shares > 1e6


def test_instance_document_of_latest_10q(clients, ticker_map):
    c = cik(ticker_map, "AAPL")
    subs = fetch_submissions(clients.sec, c)
    latest = subs.filings[subs.filings.form == "10-Q"].iloc[0]
    rows = fetch_instance_facts(clients.sec, c, latest.accession, "10-Q", latest.filing_date)
    assert any(r[1] == "revenue" for r in rows)


def test_insider_bulk_columns(clients, ticker_map):
    data = _fetch_zip(clients.sec, "2024q1")  # production path: validated before caching
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = {n.split("/")[-1].upper() for n in z.namelist()}
    assert {"SUBMISSION.TSV", "REPORTINGOWNER.TSV", "NONDERIV_TRANS.TSV"} <= names
    rows = parse_insider_zip(data, set(ticker_map["cik"].astype(int)))
    n_p = sum(r[8] == "P" for r in rows)
    n_s = sum(r[8] == "S" for r in rows)
    print(f"\ninsider 2024q1: {len(rows)} rows, {n_p} P, {n_s} S")
    assert rows and n_p > 0
    # Row layout: (accession, seq, issuer_cik, filing_date, ..., trans_date at 7, trans_code at 8)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[3]) for r in rows)
    share = sum(r[7] is not None for r in rows) / len(rows)
    assert share >= 0.95, f"only {share:.1%} of rows have a parsed trans_date (DD-MON-YYYY?)"


def test_daily_index_recent_weekday(clients):
    d, checked = TODAY - timedelta(days=1), 0
    while checked < 10:
        if d.weekday() < 5:
            checked += 1
            entries = fetch_master_index(clients.sec, d)
            if entries is not None and len(entries) > 1000:
                print(f"\ndaily index worked for {d} ({len(entries)} entries)")
                return
        d -= timedelta(days=1)
    pytest.fail("no weekday in the last 10 returned more than 1000 index entries")


def test_fred(settings, clients):
    if not settings.fred_api_key:
        pytest.skip("FRED_API_KEY not set")
    rows = fetch_series(clients.fred, settings.fred_api_key, "NFCI", TODAY - timedelta(days=60))
    assert rows
