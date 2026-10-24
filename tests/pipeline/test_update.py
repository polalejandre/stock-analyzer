import json
import logging
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from typer.testing import CliRunner

from analyzer import cli
from analyzer.config import Settings, load_rules
from analyzer.data.edgar.insider_bulk import InsiderSync
from analyzer.data.http import FetchError
from analyzer.data.price_sync import PriceSyncReport
from analyzer.pipeline import update
from analyzer.pipeline.update import (
    FAILURE_LIMIT,
    StepResult,
    UpdateContext,
    build_context,
    run_update,
    step_edgar,
    step_macro,
    step_prices,
    step_universe,
)
from analyzer.store.db import connect, get_state, migrate, set_state
from analyzer.universe.membership import INDEXES
from analyzer.universe.wikipedia import SnapshotSync
from tests.helpers import FakeProvider, FakeResponse, bars, fake_client

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
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setenv("FRED_API_KEY", "k")  # every step is selected by default, macro included
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
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    result = CliRunner().invoke(cli.app, ["update", "--step", "nope"])
    assert result.exit_code == 2


def test_cli_passes_options_to_the_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setenv("FRED_API_KEY", "k")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    seen = {}

    def fake_build(settings, rules, today, tickers):
        seen["build"] = (today, tickers)

    def fake_run(ctx, steps):
        seen["steps"] = steps
        return []

    monkeypatch.setattr(cli, "build_context", fake_build)
    monkeypatch.setattr(cli, "run_update", fake_run)
    result = CliRunner().invoke(cli.app, ["update", "--step", "macro", "--step", "prices",
                                          "--step", "macro", "--tickers", "brk.b, aapl",
                                          "--today", "2026-10-09"])
    assert result.exit_code == 0
    assert seen["build"] == (TODAY, {"BRK-B", "AAPL"})
    assert seen["steps"] == ["prices", "macro"]  # canonical order, each once


def test_cli_rejects_a_malformed_date(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
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
    for snapshot_date in ("2026-09-01", "2026-10-09"):
        ctx.conn.execute("INSERT INTO index_snapshots VALUES ('sp500', ?, 1, 'AAPL', NULL, NULL, NULL, NULL, NULL)",
                         (snapshot_date,))
    result = step_universe(ctx)
    assert screen == ["refresh_tickers"]  # no screen here: index prices are not stored yet
    assert result.attempted == 2 * len(INDEXES) + 1 + 1  # + aliases
    assert result.failed == []
    assert result.detail == "tickers=7 aliases=0 recycled=0 excluded=0"
    assert result.latest_data_date == date(2026, 10, 9)  # the newest snapshot actually stored


def test_step_universe_freshness_is_the_stored_snapshot_not_today(ctx, screen):
    ctx.conn.execute("INSERT INTO index_snapshots VALUES ('sp500', '2026-10-01', 1, 'AAPL', NULL, NULL, NULL, NULL, NULL)")
    assert step_universe(ctx).latest_data_date == date(2026, 10, 1)
    ctx.conn.execute("DELETE FROM index_snapshots")
    assert step_universe(ctx).latest_data_date is None


def test_step_universe_one_failing_index_does_not_skip_the_other(ctx, screen, monkeypatch):
    synced = []

    def flaky(conn, wiki, name, today):
        synced.append(name)
        if name == INDEXES[0]:
            raise RuntimeError("wikipedia 503")
        return SnapshotSync(attempted=3, failed=[("sp400 2026-09-01", "bad table")])

    monkeypatch.setattr(update, "sync_index_snapshots", flaky)
    result = step_universe(ctx)
    assert synced == list(INDEXES)
    assert result.failed == [(INDEXES[0], "RuntimeError: wikipedia 503"),
                             ("sp400 2026-09-01", "bad table")]
    assert result.attempted == 1 + 3 + 1 + 1  # failed index, other index, ticker map, aliases
    assert result.phase_failed and not result.healthy
    assert result.detail == "tickers=7 aliases=0 recycled=0 excluded=0"  # the rest of the step still ran


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
        self.tickers_error: Exception | None = None
        self.refreshed: list[tuple] = []
        self.scanned: list[set[int]] = []
        self.instance: list[int] = []
        self.insider: list[set[int]] = []
        self.reextracted: list[list[int]] = []
        self.ticker_refreshes = 0

    def reextract(self, conn, sec, ciks):
        self.reextracted.append(list(ciks))
        return len(self.reextracted[-1])

    def retry_state(self):
        """The persisted {cik: consecutive failures}, or None before anything was written."""
        raw = get_state(self.conn, "edgar_retry_ciks")
        return None if raw is None else json.loads(raw)

    def retry_list(self):
        state = self.retry_state()
        return None if state is None else sorted(int(c) for c in state)

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
        if self.tickers_error:
            raise self.tickers_error
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
    monkeypatch.setattr(update, "reextract_facts_if_tags_changed", w.reextract)
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


def test_edgar_reextracts_stored_companies_before_new_ones_are_backfilled(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2, known=False)
    result = step_edgar(ctx)
    assert world.reextracted == [[1]]  # BBB is backfilled with the current tag map anyway
    assert "reextracted=1" in result.detail


def test_edgar_refreshes_facts_when_a_periodic_form_was_filed(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    world.changed = {1: {"8-K", "10-Q"}, 2: {"4"}}
    step_edgar(ctx)
    assert world.refreshed == [(1, timedelta(0), True), (2, timedelta(0), False)]


def test_edgar_ticker_subset_scans_everyone_but_refreshes_only_the_subset(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    add_company(ctx.conn, "CCC", 3)
    ctx.tickers = {"BBB"}
    world.changed = {1: {"10-K"}, 2: {"8-K"}}
    step_edgar(ctx)
    assert world.scanned == [{1, 2, 3}]  # the daily-index pointer is shared: never scan a subset
    assert world.refreshed == [(2, timedelta(0), False)]
    assert world.instance == [2]
    assert world.insider == [{2}]
    assert world.retry_list() == [1]  # AAA changed outside the subset: owed to the next full run

    world.refreshed.clear()
    world.changed = {}
    ctx.tickers = None
    step_edgar(ctx)
    assert world.refreshed == [(1, timedelta(0), True)]
    assert world.retry_list() == []


def test_edgar_attempted_counts_every_company_in_each_phase(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    add_company(ctx.conn, "CCC", 3, known=False)
    world.changed = {1: {"10-K"}}
    set_state(ctx.conn, "edgar_retry_ciks", '{"2": 0}')
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
    set_state(ctx.conn, "edgar_retry_ciks", json.dumps({"3": 2, "1": 0}))
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
    set_state(ctx.conn, "edgar_retry_ciks", '{"1": 3}')
    ctx.tickers = {"BBB"}
    step_edgar(ctx)
    assert world.refreshed == []
    assert world.retry_state() == {"1": 3}  # untouched, failure count included


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
    set_state(ctx.conn, "edgar_retry_ciks", '{"2": 0}')
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


def test_edgar_backfill_interrupted_before_the_facts_is_owed_and_finished_next_run(
        ctx, world, monkeypatch):
    add_company(ctx.conn, "AAA", 1, known=False)

    def interrupted(conn, sec, cik, *, max_age, facts=True, now=None):
        conn.execute("INSERT INTO companies VALUES (?, 'Co', NULL, NULL, 'now')", (cik,))
        raise KeyboardInterrupt  # killed inside the companyfacts fetch

    monkeypatch.setattr(update, "refresh_company", interrupted)
    with pytest.raises(KeyboardInterrupt):
        step_edgar(ctx)
    assert world.retry_list() == [1]  # the company is "known" now, but only the list remembers it

    monkeypatch.setattr(update, "refresh_company", world.refresh_company)
    step_edgar(ctx)
    assert world.refreshed == [(1, timedelta(0), True)]
    assert world.retry_list() == []


def test_edgar_retry_list_counts_consecutive_failures_and_gives_up(ctx, world, caplog):
    caplog.set_level(logging.WARNING)
    add_company(ctx.conn, "AAA", 1)
    world.changed = {1: {"10-K"}}
    world.refresh_fails = {1}
    step_edgar(ctx)
    assert world.retry_state() == {"1": 1}
    world.changed = {}
    for failures in range(2, 10):
        step_edgar(ctx)
        assert world.retry_state() == {"1": failures}
    assert "giving up" not in caplog.text

    step_edgar(ctx)  # the 10th failure in a row
    assert world.retry_state() == {}
    assert "giving up retrying CIK 1 after 10 consecutive failed refreshes" in caplog.text
    world.refreshed.clear()
    step_edgar(ctx)
    assert world.refreshed == []


def test_edgar_a_success_clears_the_failure_count(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    set_state(ctx.conn, "edgar_retry_ciks", '{"1": 7}')
    step_edgar(ctx)
    assert world.retry_state() == {}


def test_edgar_retry_list_accepts_the_old_list_format(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    set_state(ctx.conn, "edgar_retry_ciks", "[1]")
    world.refresh_fails = {1}
    step_edgar(ctx)
    assert world.refreshed == [(1, timedelta(0), True)]
    assert world.retry_state() == {"1": 1}


def test_edgar_retry_list_drops_companies_no_longer_tracked(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    world.merges = {1: [99]}
    set_state(ctx.conn, "edgar_retry_ciks", '{"1": 4, "77": 2, "99": 1}')
    world.refresh_fails = {1, 99}
    step_edgar(ctx)
    assert world.retry_state() == {"1": 5, "99": 2}  # 77 is neither tracked nor a predecessor


def test_edgar_unreadable_retry_state_is_ignored(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    set_state(ctx.conn, "edgar_retry_ciks", "not json")
    assert step_edgar(ctx).failed == []


def _crowd(conn, n=40):
    for i in range(n):
        add_company(conn, f"T{i}", 100 + i)


@pytest.mark.parametrize("break_phase, item", [
    (lambda w: setattr(w, "scan_error", FetchError("invalid response body for u")), "daily-index"),
    (lambda w: setattr(w, "insider_error", ValueError("bad quarter")), "insider"),
    (lambda w: setattr(w, "tickers_error", RuntimeError("db locked")), "refresh-tickers"),
])
def test_a_failed_edgar_phase_is_unhealthy_even_when_it_is_a_tiny_share_of_the_items(
        ctx, world, break_phase, item):
    _crowd(ctx.conn)
    break_phase(world)
    result = step_edgar(ctx)
    assert failed_items(result) == [item]
    assert result.failure_rate < FAILURE_LIMIT
    assert result.phase_failed and not result.healthy

    run_update(ctx, ["edgar"], funcs={"edgar": lambda c: result})
    assert ctx.conn.execute("SELECT provider FROM provider_status").fetchall() == []
    assert ctx.conn.execute("SELECT step, item FROM fetch_failures").fetchall() == [("edgar", item)]


def test_edgar_late_failure_keeps_the_per_company_failures(ctx, world):
    add_company(ctx.conn, "AAA", 1)
    add_company(ctx.conn, "BBB", 2)
    ctx.conn.execute("INSERT INTO filings VALUES ('a-1', 1, '10-Q', '2026-10-01', NULL, NULL, NULL)")
    world.instance_fails = {2}
    world.tickers_error = RuntimeError("db locked")
    result = step_edgar(ctx)
    assert failed_items(result) == ["2 instance", "refresh-tickers"]
    assert result.latest_data_date == date(2026, 10, 1)  # the rest of the step still ran


def test_edgar_per_company_failures_alone_are_judged_by_the_rate(ctx, world):
    _crowd(ctx.conn)
    world.instance_fails = {100}
    result = step_edgar(ctx)
    assert failed_items(result) == ["100 instance"]
    assert not result.phase_failed and result.healthy


# --- whole-step health ------------------------------------------------------------------------

def status_providers(ctx):
    return [r[0] for r in ctx.conn.execute("SELECT provider FROM provider_status")]


def test_a_failed_young_screen_makes_prices_unhealthy(ctx, screen, monkeypatch):
    monkeypatch.setattr(update, "screen_young_listings",
                        lambda *a: (_ for _ in ()).throw(FetchError("sec is down")))
    result = step_prices(ctx)
    assert result.phase_failed and not result.healthy
    assert result.failure_rate < FAILURE_LIMIT  # one item among ~hundreds
    run_update(ctx, ["prices"])
    assert status_providers(ctx) == []
    assert ctx.conn.execute("SELECT item FROM fetch_failures").fetchall() == [("young-screen",)]


def test_a_healthy_prices_step_updates_provider_status(ctx, screen):
    set_state(ctx.conn, "young_screen_last", TODAY.isoformat())
    result = step_prices(ctx)
    assert result.healthy and not result.phase_failed
    run_update(ctx, ["prices"])
    assert status_providers(ctx) == ["prices"]


def test_missing_spy_makes_prices_unhealthy(ctx, screen, monkeypatch):
    set_state(ctx.conn, "young_screen_last", TODAY.isoformat())
    monkeypatch.setattr(update, "sync_prices",
                        lambda *a: PriceSyncReport(missing=["SPY"]))
    result = step_prices(ctx)
    assert result.failed == [("SPY", "no price data from any provider")]
    assert result.failure_rate < FAILURE_LIMIT
    assert result.phase_failed and not result.healthy

    monkeypatch.setattr(update, "sync_prices", lambda *a: PriceSyncReport(missing=["XLK"]))
    assert step_prices(ctx).healthy  # any other single symbol is just one item


def test_a_step_that_raises_is_unhealthy(ctx):
    def boom(c):
        raise RuntimeError("down")

    [result] = run_update(ctx, ["macro"], funcs={"macro": boom})
    assert result.phase_failed and not result.healthy
    assert status_providers(ctx) == []


def test_the_cli_fails_on_a_phase_failure_below_the_item_limit(monkeypatch, ctx, world):
    _crowd(ctx.conn)
    world.scan_error = FetchError("invalid response body for u")  # SEC daily index down
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setattr(cli, "build_context", lambda *a, **k: ctx)
    result = CliRunner().invoke(cli.app, ["update", "--step", "edgar"])
    assert result.exit_code == 1
    assert "FAILED: edgar (provider_status not updated)" in result.output
    assert "daily-index" in result.output
    assert status_providers(ctx) == []


def test_cli_reports_problems_on_stderr_and_the_summary_on_stdout(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setattr(cli, "build_context", lambda *a, **k: None)
    bad = StepResult("prices", attempted=500, failed=[("SPY", "no data")], phase_failed=True)
    monkeypatch.setattr(cli, "run_update", lambda ctx, steps: [bad])
    result = CliRunner().invoke(cli.app, ["update", "--step", "prices"])
    assert result.exit_code == 1
    assert "prices" in result.stdout and "attempted=500" in result.stdout
    assert "FAILED" not in result.stdout and "! SPY" not in result.stdout
    assert "FAILED: prices (provider_status not updated)" in result.stderr
    assert "! SPY: no data" in result.stderr


# --- CLI argument and configuration checks ---------------------------------------------------

@pytest.fixture
def no_build(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("nothing may run after a configuration error")

    monkeypatch.setattr(cli, "build_context", refuse)
    monkeypatch.setattr(cli, "run_update", refuse)


def test_cli_checks_the_fred_key_before_running_when_macro_is_selected(monkeypatch, no_build):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setenv("FRED_API_KEY", "")  # overrides any key in a developer's .env
    for args in (["update"], ["update", "--step", "macro"]):
        result = CliRunner().invoke(cli.app, args)
        assert result.exit_code == 2
        assert "FRED_API_KEY is not set" in result.stderr


def test_cli_does_not_need_the_fred_key_without_the_macro_step(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setenv("FRED_API_KEY", "")
    monkeypatch.setattr(cli, "build_context", lambda *a, **k: None)
    monkeypatch.setattr(cli, "run_update", lambda ctx, steps: [])
    assert CliRunner().invoke(cli.app, ["update", "--step", "prices"]).exit_code == 0


@pytest.mark.parametrize("value", [",", " , ", ""])
def test_cli_rejects_tickers_that_name_nothing(monkeypatch, no_build, value):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setenv("FRED_API_KEY", "k")
    result = CliRunner().invoke(cli.app, ["update", "--tickers", value])
    assert result.exit_code == 2
    assert "--tickers" in result.stderr


def test_cli_treats_an_unreadable_rules_file_as_a_configuration_error(monkeypatch, no_build):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setenv("FRED_API_KEY", "k")

    def broken():
        raise KeyError("universe")

    monkeypatch.setattr(cli, "load_rules", broken)
    result = CliRunner().invoke(cli.app, ["update"])
    assert result.exit_code == 2
    assert "Configuration error: KeyError: 'universe'" in result.stderr


def test_cli_unknown_step_goes_to_stderr(monkeypatch, no_build):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    result = CliRunner().invoke(cli.app, ["update", "--step", "nope"])
    assert result.exit_code == 2
    assert "Unknown step(s): nope" in result.stderr


# --- step_macro and build_context ------------------------------------------------------------

FRED_KEY = "FAKEKEY-9f8e7d6c"


def macro_world(ctx, tmp_path, handler):
    client, session = fake_client(tmp_path, handler=handler)
    ctx.clients = SimpleNamespace(fred=client)
    ctx.settings = Settings(sec_user_agent="T t@test.invalid", fred_api_key=FRED_KEY,
                            data_dir=tmp_path)
    return session


def observations(series_id, params):
    day = "2026-10-08" if series_id == "WALCL" else "2026-10-02"
    return FakeResponse(json_data={"observations": [{"date": day, "value": "1.5"}]})


def test_step_macro_reports_every_series_and_the_newest_date(ctx, tmp_path):
    session = macro_world(ctx, tmp_path,
                          lambda url, p: observations(p["series_id"], p))
    result = step_macro(ctx)
    assert result.attempted == len(session.calls) == 6
    assert result.failed == []
    assert result.latest_data_date == date(2026, 10, 8)
    assert result.healthy


def test_step_macro_failed_series_are_items_and_never_show_the_key(ctx, tmp_path):
    def handler(url, p):
        if p["series_id"] == "M2SL":
            return FakeResponse(status_code=500)
        return observations(p["series_id"], p)

    macro_world(ctx, tmp_path, handler)
    result = step_macro(ctx)
    assert failed_items(result) == ["M2SL"]
    assert result.attempted == 6
    assert FRED_KEY not in repr(result)
    assert not result.healthy  # one series of six is above the 5% limit
    assert ctx.conn.execute("SELECT COUNT(DISTINCT series_id) FROM macro").fetchone()[0] == 5


def test_cli_output_and_logs_never_contain_the_fred_key(monkeypatch, ctx, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SEC_USER_AGENT", "T t@test.invalid")
    monkeypatch.setenv("FRED_API_KEY", FRED_KEY)
    macro_world(ctx, tmp_path, lambda url, p: FakeResponse(status_code=500))
    monkeypatch.setattr(cli, "build_context", lambda settings, rules, today, tickers: ctx)
    result = CliRunner().invoke(cli.app, ["update", "--step", "macro"])
    assert result.exit_code == 1
    assert "FAILED: macro" in result.output
    assert FRED_KEY not in result.output
    assert FRED_KEY not in caplog.text


def test_build_context_wires_the_database_clients_and_providers(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("build_context must not touch the network")

    monkeypatch.setattr("requests.Session.request", no_network)
    settings = Settings(sec_user_agent="T t@test.invalid", fred_api_key=None,
                        data_dir=tmp_path / "data")
    built = build_context(settings, load_rules(), TODAY, {"AAPL"})
    assert built.settings is settings
    assert (built.today, built.tickers) == (TODAY, {"AAPL"})
    assert len(built.run_id) == 12
    assert [p.name for p in built.price_providers] == ["yfinance", "nasdaq"]
    assert built.clients.sec.headers["User-Agent"] == "T t@test.invalid"
    assert (tmp_path / "data" / "analyzer.db").exists()
    tables = {r[0] for r in built.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"prices", "provider_status", "fetch_failures", "sync_state"} <= tables
    assert build_context(settings, load_rules(), TODAY, None).tickers is None
    assert build_context(settings, load_rules(), TODAY, None).run_id != built.run_id


def test_step_universe_lists_given_up_months_without_counting_them(ctx, screen, monkeypatch):
    monkeypatch.setattr(update, "sync_index_snapshots", lambda conn, wiki, name, today: SnapshotSync(
        attempted=1, skipped=[f"{name} 2013-04-01"] if name == "sp500" else []))
    result = step_universe(ctx)
    assert result.attempted == len(INDEXES) + 1 + 1  # + aliases
    assert result.failed == [] and result.healthy
    assert result.detail == "tickers=7 aliases=0 recycled=0 excluded=0 skipped_months=[sp500 2013-04-01]"


def test_step_universe_alias_failure_is_recorded_but_keeps_other_results(ctx, screen, monkeypatch):
    def boom(conn, wiki):
        raise RuntimeError("alias boom")

    monkeypatch.setattr(update, "sync_aliases", boom)
    result = step_universe(ctx)
    assert [name for name, _ in result.failed] == ["aliases"]
    assert "alias boom" in result.failed[0][1]
    assert result.detail.startswith("tickers=7")
    assert result.attempted == 2 * len(INDEXES) + 1 + 1
