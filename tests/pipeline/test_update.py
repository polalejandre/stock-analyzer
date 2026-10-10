import json
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from typer.testing import CliRunner

from analyzer import cli
from analyzer.config import load_rules
from analyzer.data.edgar.insider_bulk import InsiderSync
from analyzer.data.http import FetchError
from analyzer.data.price_sync import PriceSyncReport
from analyzer.pipeline import update
from analyzer.pipeline.update import (
    StepResult,
    UpdateContext,
    run_update,
    step_edgar,
    step_prices,
    step_universe,
)
from analyzer.store.db import connect, get_state, migrate, set_state
from analyzer.universe.membership import INDEXES
from analyzer.universe.wikipedia import SnapshotSync
from tests.helpers import FakeProvider, bars

TODAY = date(2026, 10, 9)


@pytest.fixture
def ctx(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return UpdateContext(conn=conn, settings=None, rules=load_rules(), clients=None,
                         price_providers=[], today=TODAY, run_id="run1")


def test_failed_step_is_recorded_and_others_still_run(ctx):
    def ok(c):
        return StepResult("prices", attempted=100, failed=[("X", "no data")],
                          latest_data_date=date(2026, 10, 9))

    def boom(c):
        raise RuntimeError("wikipedia down")

    results = run_update(ctx, ["universe", "prices"], funcs={"universe": boom, "prices": ok})
    assert [r.name for r in results] == ["universe", "prices"]
    assert results[0].failure_rate == 1.0
    assert results[1].failure_rate == 0.01
    status = ctx.conn.execute("SELECT provider, latest_data_date FROM provider_status").fetchall()
    assert status == [("prices", "2026-10-09")]
    failures = ctx.conn.execute("SELECT step, item FROM fetch_failures ORDER BY step").fetchall()
    assert failures == [("prices", "X"), ("universe", "*")]


def test_step_prices_refreshes_active_symbols(ctx):
    set_state(ctx.conn, "young_screen_last", TODAY.isoformat())  # the weekly screen is not due
    ctx.conn.execute("INSERT INTO index_membership VALUES ('sp500', 'AAPL', '2010-01-01', NULL)")
    data = {s: bars(["2026-10-08", "2026-10-09"], [1.0, 2.0]) for s in ("AAPL", "SPY", "XLK")}
    ctx.price_providers.append(FakeProvider("yfinance", data))
    result = step_prices(ctx)
    assert result.latest_data_date == date(2026, 10, 9)
    assert ("AAPL", "no price data from any provider") not in result.failed
    assert len(result.failed) == result.attempted - 3  # only AAPL, SPY and XLK have data


def test_step_prices_with_ticker_subset(ctx):
    ctx.tickers = {"AAPL"}
    ctx.conn.execute("INSERT INTO index_membership VALUES ('sp500', 'AAPL', '2010-01-01', NULL)")
    data = {s: bars(["2026-10-09"], [1.0]) for s in ("AAPL", "SPY")}
    ctx.price_providers.append(FakeProvider("yfinance", data))
    result = step_prices(ctx)
    assert result.attempted == 2 and result.failed == []


def test_cli_exit_codes(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "build_context", lambda *a, **k: None)
    runner = CliRunner()

    monkeypatch.setattr(cli, "run_update", lambda ctx, steps: [StepResult("prices", attempted=10)])
    assert runner.invoke(cli.app, ["update"]).exit_code == 0

    bad = StepResult("prices", attempted=10, failed=[("A", "x")])
    monkeypatch.setattr(cli, "run_update", lambda ctx, steps: [bad])
    result = runner.invoke(cli.app, ["update", "--step", "prices"])
    assert result.exit_code == 1
    assert "prices" in result.output


def test_cli_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "no-email")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["update"])
    assert result.exit_code == 2
    assert "contact email" in result.output


def test_unknown_step_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    result = CliRunner().invoke(cli.app, ["update", "--step", "nope"])
    assert result.exit_code == 2


def test_cli_passes_options_to_the_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    seen = {}

    def fake_build(settings, rules, today, tickers):
        seen["build"] = (today, tickers)

    def fake_run(ctx, steps):
        seen["steps"] = steps
        return []

    monkeypatch.setattr(cli, "build_context", fake_build)
    monkeypatch.setattr(cli, "run_update", fake_run)
    result = CliRunner().invoke(cli.app, ["update", "--step", "prices", "--step", "macro",
                                          "--tickers", "brk.b, aapl", "--today", "2026-10-09"])
    assert result.exit_code == 0
    assert seen["build"] == (TODAY, {"BRK-B", "AAPL"})
    assert seen["steps"] == ["prices", "macro"]


def test_cli_rejects_a_malformed_date(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(cli.app, ["update", "--today", "10/09/2026"])
    assert result.exit_code == 2
    assert "--today" in result.output


# --- step_universe / the weekly young-listing screen --------------------------------------------

TICKER_MAP = pd.DataFrame({"cik": [1], "name": ["Acme"], "ticker": ["ACME"], "exchange": ["Nasdaq"]})


@pytest.fixture
def screen(ctx, monkeypatch):
    """Stubs for everything step_prices/step_universe reach for; `calls` records their order."""
    ctx.clients = SimpleNamespace(sec="sec", wiki="wiki")
    ctx.price_providers.append(FakeProvider("yfinance", {}))
    calls: list = []

    def fake_sync_prices(conn, providers, active, historical, today):
        calls.append("sync_prices")
        return PriceSyncReport()

    def fake_screen(conn, primary, ticker_map, rules, today):
        calls.append(("screen", primary, ticker_map, rules, today))
        return ["ACME", "NEWCO"]

    def fake_refresh_tickers(conn, ticker_map, today):
        calls.append("refresh_tickers")
        return 7

    monkeypatch.setattr(update, "sync_prices", fake_sync_prices)
    monkeypatch.setattr(update, "screen_young_listings", fake_screen)
    monkeypatch.setattr(update, "load_ticker_map", lambda sec: TICKER_MAP)
    monkeypatch.setattr(update, "refresh_tickers", fake_refresh_tickers)
    monkeypatch.setattr(update, "sync_index_snapshots",
                        lambda conn, wiki, name, today: SnapshotSync(attempted=2))
    return calls


def test_step_universe_does_not_screen_young_listings(ctx, screen):
    result = step_universe(ctx)
    assert screen == ["refresh_tickers"]  # no screen here: index prices are not stored yet
    assert result.attempted == 2 * len(INDEXES) + 1
    assert result.failed == []
    assert result.detail == "tickers=7"
    assert result.latest_data_date == TODAY


def test_young_screen_runs_after_prices_are_synced(ctx, screen):
    result = step_prices(ctx)
    assert screen[0] == "sync_prices"
    _, primary, ticker_map, rules, today = screen[1]
    assert primary is ctx.price_providers[0]
    assert ticker_map is TICKER_MAP
    assert (rules, today) == (ctx.rules.universe, TODAY)  # no index_tickers argument
    assert screen[2] == "refresh_tickers"  # young listings reach the tickers table this run
    assert get_state(ctx.conn, "young_screen_last") == TODAY.isoformat()
    assert "young_kept=2" in result.detail


@pytest.mark.parametrize("days_ago, runs", [(6, False), (7, True), (30, True)])
def test_young_screen_is_weekly(ctx, screen, days_ago, runs):
    set_state(ctx.conn, "young_screen_last", (TODAY - timedelta(days=days_ago)).isoformat())
    step_prices(ctx)
    assert (len(screen) > 1) is runs
    expected = TODAY.isoformat() if runs else (TODAY - timedelta(days=days_ago)).isoformat()
    assert get_state(ctx.conn, "young_screen_last") == expected


def test_young_screen_is_skipped_for_a_ticker_subset(ctx, screen):
    ctx.tickers = {"AAPL"}
    step_prices(ctx)
    assert screen == ["sync_prices"]
    assert get_state(ctx.conn, "young_screen_last") is None


def test_failed_young_screen_is_one_failed_item_and_is_retried(ctx, screen, monkeypatch):
    def broken(*args):
        raise FetchError("sec is down")

    monkeypatch.setattr(update, "screen_young_listings", broken)
    ctx.conn.execute("INSERT INTO index_membership VALUES ('sp500', 'AAPL', '2010-01-01', NULL)")
    plain = step_prices(ctx)  # the price report survives the failed screen
    assert plain.failed == [("young-screen", "FetchError: sec is down")]
    assert plain.attempted == len(update.ALL_REFERENCE_ETFS) + 1 + 1  # symbols, AAPL, the screen
    assert get_state(ctx.conn, "young_screen_last") is None  # so the next run tries again
    assert "refresh_tickers" not in screen


# --- step_edgar ------------------------------------------------------------------------------

class EdgarWorld:
    """Fakes for the EDGAR functions step_edgar calls, with switches for each failure."""

    def __init__(self, conn):
        self.conn = conn
        self.merges: dict[int, list[int]] = {}
        self.changed: dict[int, set[str]] = {}
        self.refresh_fails: set[int] = set()
        self.partial_fails: set[int] = set()  # submissions stored, then companyfacts fails
        self.instance_fails: set[int] = set()
        self.scan_error: Exception | None = None
        self.insider_error: Exception | None = None
        self.refreshed: list[tuple] = []
        self.scanned: list[set[int]] = []
        self.instance: list[int] = []
        self.insider: list[set[int]] = []
        self.ticker_refreshes = 0

    def retry_list(self):
        raw = get_state(self.conn, "edgar_retry_ciks")
        return None if raw is None else json.loads(raw)

    def refresh_company(self, conn, sec, cik, *, max_age, facts=True, now=None):
        self.refreshed.append((cik, max_age, facts))
        if cik in self.refresh_fails:
            raise FetchError(f"submissions {cik} unavailable")
        conn.execute("INSERT OR REPLACE INTO companies VALUES (?, 'Co', NULL, NULL, 'now')", (cik,))
        if cik in self.partial_fails:
            raise FetchError(f"companyfacts {cik} unavailable")

    def scan(self, conn, sec, ciks, today):
        self.scanned.append(set(ciks))
        if self.scan_error:
            raise self.scan_error
        return {c: f for c, f in self.changed.items() if c in ciks}

    def instance_fallback(self, conn, sec, cik, today):
        self.instance.append(cik)
        if cik in self.instance_fails:
            raise RuntimeError(f"instance {cik} broke")

    def sync_insider(self, conn, sec, ciks, today):
        self.insider.append(set(ciks))
        if self.insider_error:
            raise self.insider_error
        return InsiderSync(loaded=["2026q2"], xml_filings=3)

    def refresh_tickers(self, conn, ticker_map, today):
        self.ticker_refreshes += 1
        return 0


@pytest.fixture
def world(ctx, monkeypatch):
    ctx.clients = SimpleNamespace(sec="sec")
    w = EdgarWorld(ctx.conn)
    monkeypatch.setattr(update, "load_ticker_map", lambda sec: TICKER_MAP)
    monkeypatch.setattr(update, "load_cik_merges", lambda ticker_map: w.merges)
    monkeypatch.setattr(update, "refresh_company", w.refresh_company)
    monkeypatch.setattr(update, "scan_daily_indexes", w.scan)
    monkeypatch.setattr(update, "apply_instance_fallback", w.instance_fallback)
    monkeypatch.setattr(update, "sync_insider", w.sync_insider)
    monkeypatch.setattr(update, "refresh_tickers", w.refresh_tickers)
    return w


def add_company(conn, ticker, cik, known=True):
    conn.execute("INSERT INTO tickers (ticker, cik, updated_at) VALUES (?, ?, 'now')", (ticker, cik))
    if known:
        conn.execute("INSERT INTO companies VALUES (?, 'Co', NULL, NULL, 'now')", (cik,))


def failed_items(result):
    return [item for item, _ in result.failed]


def test_edgar_backfills_new_companies_then_refreshes_changed_ones(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2, known=False)
    ctx.conn.execute("INSERT INTO tickers (ticker, updated_at) VALUES ('NOCIK', 'now')")
    world.merges = {2: [99]}  # BBB absorbed company 99
    world.changed = {1: {"8-K"}}
    result = step_edgar(ctx)

    assert world.refreshed == [(2, timedelta(hours=20), True), (99, None, True),
                               (1, timedelta(0), False)]  # no periodic form: submissions only
    assert world.scanned == [{1}]  # only tracked companies known before this run
    assert world.instance == [1, 2]
    assert world.insider == [{1, 2}]
    assert world.ticker_refreshes == 1
    assert result.failed == []


def test_edgar_refreshes_facts_when_a_periodic_form_was_filed(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    world.changed = {1: {"8-K", "10-Q"}, 2: {"4"}}
    step_edgar(ctx)
    assert world.refreshed == [(1, timedelta(0), True), (2, timedelta(0), False)]


def test_edgar_ticker_subset_limits_the_companies(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    ctx.tickers = {"BBB"}
    step_edgar(ctx)
    assert world.scanned == [{2}]
    assert world.instance == [2]
    assert world.insider == [{2}]


def test_edgar_attempted_counts_every_company_in_each_phase(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    add_company(ctx.conn, "CCC", 3, known=False)
    world.changed = {1: {"10-K"}}
    set_state(ctx.conn, "edgar_retry_ciks", "[2]")
    result = step_edgar(ctx)
    # 1 backfilled + 2 refreshed (changed 1, retried 2) + 3 instance fallbacks + daily index + insider
    assert result.attempted == 1 + 2 + 3 + 1 + 1
    assert result.failed == []


def test_edgar_attempted_is_not_inflated_when_an_instance_fallback_fails(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    world.instance_fails = {2}
    result = step_edgar(ctx)
    assert result.attempted == 2 + 1 + 1  # instance phase counts each company once
    assert failed_items(result) == ["2 instance"]
    assert world.insider and world.ticker_refreshes == 1  # later phases still ran


def test_edgar_failed_refresh_is_retried_on_the_next_run(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    world.changed = {1: {"10-K"}, 2: {"8-K"}}  # the daily-index pointer moves past these days
    world.refresh_fails = {1}
    first = step_edgar(ctx)
    assert failed_items(first) == ["1"]
    assert world.retry_list() == [1]  # 2 succeeded, 1 is remembered

    world.refreshed.clear()
    world.changed = {}  # nothing new was filed
    world.refresh_fails = set()
    second = step_edgar(ctx)
    assert world.refreshed == [(1, timedelta(0), True)]  # full refresh, facts included
    assert second.failed == []
    assert world.retry_list() == []


def test_edgar_retry_list_is_unioned_with_the_changed_set(ctx, world):
    for ticker, cik in (("AAA", 1), ("BBB", 2), ("CCC", 3)):
        add_company(ctx.conn, ticker, cik)
    set_state(ctx.conn, "edgar_retry_ciks", json.dumps([3, 1]))
    world.changed = {1: {"8-K"}, 2: {"8-K"}}
    step_edgar(ctx)
    assert sorted(world.refreshed) == [(1, timedelta(0), True),  # retried: facts forced on
                                       (2, timedelta(0), False),
                                       (3, timedelta(0), True)]
    assert world.refreshed.count((1, timedelta(0), True)) == 1  # once, not once per source
    assert world.retry_list() == []


def test_edgar_changed_companies_are_owed_before_any_refresh_runs(ctx, world, monkeypatch):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    world.changed = {1: {"10-K"}, 2: {"10-K"}}

    def interrupted(conn, sec, cik, *, max_age, facts=True, now=None):
        if cik == 2:
            raise KeyboardInterrupt  # killed part-way through; the pointer already moved
        world.refresh_company(conn, sec, cik, max_age=max_age, facts=facts)

    monkeypatch.setattr(update, "refresh_company", interrupted)
    with pytest.raises(KeyboardInterrupt):
        step_edgar(ctx)
    assert world.retry_list() == [2]  # 1 was refreshed and settled, 2 is still owed


def test_edgar_retry_list_keeps_companies_outside_a_ticker_subset(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    set_state(ctx.conn, "edgar_retry_ciks", "[1]")
    ctx.tickers = {"BBB"}
    step_edgar(ctx)
    assert world.refreshed == []
    assert world.retry_list() == [1]


def test_edgar_failed_backfill_is_remembered_and_not_refreshed_twice(ctx, world):
    add_company(ctx.conn, "AAA", 1, known=False)
    world.refresh_fails = {1}
    assert failed_items(step_edgar(ctx)) == ["1"]
    assert world.retry_list() == [1]

    world.refreshed.clear()
    world.refresh_fails = set()
    assert step_edgar(ctx).failed == []
    assert world.refreshed == [(1, timedelta(hours=20), True)]  # the backfill, once
    assert world.retry_list() == []


def test_edgar_half_backfilled_company_gets_its_facts_on_the_next_run(ctx, world):
    add_company(ctx.conn, "AAA", 1, known=False)
    world.partial_fails = {1}  # submissions stored, companyfacts failed: the company is "known"
    assert failed_items(step_edgar(ctx)) == ["1"]
    assert world.retry_list() == [1]

    world.refreshed.clear()
    world.partial_fails = set()
    step_edgar(ctx)
    assert world.refreshed == [(1, timedelta(0), True)]
    assert world.retry_list() == []


def test_edgar_daily_index_error_is_one_failed_item_and_later_phases_run(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    ctx.conn.execute("INSERT INTO filings VALUES ('a-1', 1, '10-Q', '2026-10-01', NULL, NULL, NULL)")
    set_state(ctx.conn, "edgar_retry_ciks", "[2]")
    world.scan_error = FetchError("invalid response body for https://sec.example/master.idx")
    result = step_edgar(ctx)

    assert result.failed == [("daily-index", "FetchError: invalid response body for "
                                             "https://sec.example/master.idx")]
    assert world.refreshed == [(2, timedelta(0), True)]  # the retry list is still worked off
    assert world.instance == [1, 2]
    assert world.insider == [{1, 2}]
    assert world.ticker_refreshes == 1
    assert result.latest_data_date == date(2026, 10, 1)
    assert result.attempted == 1 + 2 + 1 + 1  # retried 2, instance x2, daily index, insider
    assert world.retry_list() == []


def test_edgar_insider_error_is_one_failed_item_and_later_phases_run(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    ctx.conn.execute("INSERT INTO filings VALUES ('a-1', 1, '10-Q', '2026-10-01', NULL, NULL, NULL)")
    world.insider_error = ValueError("insider quarter 2026q2: 9 of 10 Form 4 filings unreadable")
    result = step_edgar(ctx)

    assert result.failed == [
        ("insider", "ValueError: insider quarter 2026q2: 9 of 10 Form 4 filings unreadable")]
    assert world.ticker_refreshes == 1
    assert result.latest_data_date == date(2026, 10, 1)
    assert "insider_quarters=0" in result.detail
    assert result.attempted == 1 + 1 + 1  # instance fallback, daily index, insider


def test_edgar_clean_run_reports_the_insider_load(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    result = step_edgar(ctx)
    assert result.failed == []
    assert "insider_quarters=1" in result.detail and "form4_xml=3" in result.detail
