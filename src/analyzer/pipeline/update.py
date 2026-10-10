"""`analyzer update`: run the data steps in order, isolate failures, record freshness."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from analyzer.config import Rules, Settings
from analyzer.data.clients import Clients, make_clients
from analyzer.data.edgar.companies import load_ticker_map
from analyzer.data.edgar.daily_index import scan_daily_indexes
from analyzer.data.edgar.insider_bulk import sync_insider
from analyzer.data.edgar.instance import apply_instance_fallback
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.edgar.sync import refresh_company
from analyzer.data.edgar.tags import PERIODIC_FORMS
from analyzer.data.fred import MACRO_SERIES, sync_macro
from analyzer.data.http import FetchError
from analyzer.data.price_sync import latest_market_date, sync_prices
from analyzer.data.prices import NasdaqPrices, PriceProvider, YFinancePrices
from analyzer.store.db import connect, get_state, migrate, set_state, upsert_rows
from analyzer.universe.industry import ALL_REFERENCE_ETFS
from analyzer.universe.members import price_symbols
from analyzer.universe.membership import INDEXES
from analyzer.universe.registry import refresh_tickers
from analyzer.universe.wikipedia import sync_index_snapshots
from analyzer.universe.young import screen_young_listings

log = logging.getLogger(__name__)

STEPS = ("universe", "prices", "edgar", "macro")
FAILURE_LIMIT = 0.05
YOUNG_SCREEN_EVERY_DAYS = 7
YOUNG_SCREEN_KEY = "young_screen_last"
EDGAR_RETRY_KEY = "edgar_retry_ciks"  # sync_state: JSON {cik: consecutive failures} of owed refreshes
EDGAR_RETRY_LIMIT = 10  # a company that fails this many runs in a row is given up on


@dataclass
class StepResult:
    name: str
    attempted: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    latest_data_date: date | None = None
    detail: str = ""
    phase_failed: bool = False  # a whole phase (not one item) failed, however many items succeeded

    @property
    def failure_rate(self) -> float:
        return len(self.failed) / self.attempted if self.attempted else 0.0

    @property
    def healthy(self) -> bool:
        return not self.phase_failed and self.failure_rate <= FAILURE_LIMIT


@dataclass
class UpdateContext:
    conn: sqlite3.Connection
    settings: Settings | None
    rules: Rules
    clients: Clients | None
    price_providers: list[PriceProvider]
    today: date
    run_id: str
    tickers: set[str] | None = None


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def step_universe(ctx: UpdateContext) -> StepResult:
    res = StepResult("universe")
    skipped: list[str] = []
    for index_name in INDEXES:
        try:
            snap = sync_index_snapshots(ctx.conn, ctx.clients.wiki, index_name, ctx.today)
        except Exception as exc:  # one index failing must not skip the other
            log.exception("snapshot sync for %s failed", index_name)
            res.attempted += 1
            res.failed.append((index_name, _err(exc)))
            res.phase_failed = True
            continue
        res.attempted += snap.attempted
        res.failed += snap.failed
        skipped += snap.skipped
    ticker_map = load_ticker_map(ctx.clients.sec)
    res.attempted += 1
    res.detail = f"tickers={refresh_tickers(ctx.conn, ticker_map, ctx.today)}"
    if skipped:  # historical months given up on after repeated failures; not counted as failed
        res.detail += f" skipped_months=[{', '.join(skipped)}]"
    latest = ctx.conn.execute("SELECT MAX(snapshot_date) FROM index_snapshots").fetchone()[0]
    res.latest_data_date = date.fromisoformat(latest) if latest else None
    return res


def _screen_young_listings(ctx: UpdateContext, res: StepResult) -> None:
    """Weekly. Runs after the index and ETF prices are stored: on a fresh database the screen
    would otherwise download ~900 index names itself, only for the sync to fetch them again."""
    last = get_state(ctx.conn, YOUNG_SCREEN_KEY)
    if last is not None and (ctx.today - date.fromisoformat(last)).days < YOUNG_SCREEN_EVERY_DAYS:
        return
    res.attempted += 1
    try:
        ticker_map = load_ticker_map(ctx.clients.sec)
        kept = screen_young_listings(ctx.conn, ctx.price_providers[0], ticker_map,
                                     ctx.rules.universe, ctx.today)
        refresh_tickers(ctx.conn, ticker_map, ctx.today)  # so edgar tracks them in this same run
    except Exception as exc:  # the prices above are good; the screen is retried next run
        log.exception("young-listing screen failed")
        res.failed.append(("young-screen", _err(exc)))
        res.phase_failed = True
        return
    set_state(ctx.conn, YOUNG_SCREEN_KEY, ctx.today.isoformat())
    res.detail += f" young_kept={len(kept)}"


def step_prices(ctx: UpdateContext) -> StepResult:
    active, historical = price_symbols(ctx.conn, ctx.today, ctx.rules.universe)
    active = sorted(set(active) | set(ALL_REFERENCE_ETFS))
    if ctx.tickers is not None:
        active = sorted((set(active) & ctx.tickers) | {"SPY"})
        historical = []
    report = sync_prices(ctx.conn, ctx.price_providers, active, historical, ctx.today)
    res = StepResult(
        "prices",
        attempted=len(active),
        failed=[(s, "no price data from any provider") for s in report.missing],
        latest_data_date=latest_market_date(ctx.conn),
        detail=(f"updated={len(report.updated)} fallback={len(report.via_fallback)} "
                f"refetched={len(report.refetched)} "
                f"historical_missing={len(report.historical_missing)}"),
    )
    res.phase_failed = "SPY" in report.missing  # SPY is the market clock behind latest_data_date
    if ctx.tickers is None:
        _screen_young_listings(ctx, res)
    return res


def _load_retry(conn: sqlite3.Connection) -> dict[int, int]:
    """{cik: consecutive failed refreshes}. Unreadable state or entries only cost a retry."""
    try:
        raw = json.loads(get_state(conn, EDGAR_RETRY_KEY) or "{}")
    except ValueError:
        return {}
    if isinstance(raw, list):  # the first format: a bare list of CIKs
        return {c: 0 for c in raw if isinstance(c, int)}
    owed: dict[int, int] = {}
    for cik, failures in (raw.items() if isinstance(raw, dict) else ()):
        try:
            owed[int(cik)] = int(failures)
        except (TypeError, ValueError):
            continue
    return owed


def _save_retry(conn: sqlite3.Connection, owed: dict[int, int]) -> None:
    set_state(conn, EDGAR_RETRY_KEY, json.dumps({str(c): owed[c] for c in sorted(owed)}))


def _owe(conn: sqlite3.Connection, owed: dict[int, int], ciks) -> None:
    """Record that these companies must be refreshed. Called BEFORE the work starts, so a run
    killed part-way (or after the daily-index pointer moved) cannot lose them."""
    new = [c for c in ciks if c not in owed]
    if new:
        owed.update(dict.fromkeys(new, 0))
        _save_retry(conn, owed)


def _paid(conn: sqlite3.Connection, owed: dict[int, int], cik: int) -> None:
    if owed.pop(cik, None) is not None:
        _save_retry(conn, owed)


def _failed(conn: sqlite3.Connection, owed: dict[int, int], cik: int) -> None:
    failures = owed.get(cik, 0) + 1
    if failures >= EDGAR_RETRY_LIMIT:
        owed.pop(cik, None)
        log.warning("giving up retrying CIK %d after %d consecutive failed refreshes", cik, failures)
    else:
        owed[cik] = failures
    _save_retry(conn, owed)


def step_edgar(ctx: UpdateContext) -> StepResult:
    """Counts one attempt per backfilled company, per refreshed company (changed or owed), per
    tracked company for the instance fallback, plus one each for the daily index and insider data.

    With a ticker subset only those companies are refreshed, but the daily index is always scanned
    for every tracked company (its pointer is shared), and the changes outside the subset are
    left on the retry list for the next full run."""
    res = StepResult("edgar")
    sec = ctx.clients.sec
    ticker_map = load_ticker_map(sec)
    merges = load_cik_merges(ticker_map)
    rows = ctx.conn.execute("SELECT ticker, cik FROM tickers WHERE cik IS NOT NULL").fetchall()
    everyone = {cik for _, cik in rows}
    tracked = {cik for t, cik in rows if ctx.tickers is None or t in ctx.tickers}
    predecessors = {p for c in tracked for p in merges.get(c, [])}
    known = {r[0] for r in ctx.conn.execute("SELECT cik FROM companies")}

    owed = _load_retry(ctx.conn)
    gone = set(owed) - everyone - {p for c in everyone for p in merges.get(c, [])}
    if gone:  # no longer tracked
        for cik in gone:
            del owed[cik]
        _save_retry(ctx.conn, owed)
    owed_at_start = set(owed)

    backfill = sorted((tracked | predecessors) - known)  # first time seen: full backfill
    for cik in backfill:
        res.attempted += 1
        _owe(ctx.conn, owed, [cik])  # submissions may be stored and the facts fetch still interrupted
        try:
            refresh_company(ctx.conn, sec, cik, max_age=None if cik in predecessors else timedelta(hours=20))
        except Exception as exc:
            res.failed.append((str(cik), _err(exc)))
            _failed(ctx.conn, owed, cik)
        else:
            _paid(ctx.conn, owed, cik)

    # The daily-index pointer moves as soon as the scan finishes, so a company whose refresh fails
    # below would otherwise be forgotten until it files again. Owed CIKs are refreshed in full.
    res.attempted += 1
    try:
        changed = scan_daily_indexes(ctx.conn, sec, everyone & known, ctx.today)
    except FetchError as exc:  # pointer unchanged: the same days are scanned next run
        res.failed.append(("daily-index", _err(exc)))
        res.phase_failed = True
        changed = {}
    _owe(ctx.conn, owed, sorted(changed))

    retry_now = (owed_at_start & (tracked | predecessors)) - set(backfill)
    for cik in sorted((set(changed) & tracked) | retry_now):
        res.attempted += 1
        try:
            refresh_company(ctx.conn, sec, cik, max_age=timedelta(0),
                            facts=cik in retry_now or bool(changed[cik] & PERIODIC_FORMS))
        except Exception as exc:
            res.failed.append((str(cik), _err(exc)))
            _failed(ctx.conn, owed, cik)
        else:
            _paid(ctx.conn, owed, cik)

    for cik in sorted(tracked):
        res.attempted += 1
        try:
            apply_instance_fallback(ctx.conn, sec, cik, ctx.today)
        except Exception as exc:
            res.failed.append((f"{cik} instance", _err(exc)))

    res.attempted += 1
    quarters, xml_filings = 0, 0
    try:
        insider = sync_insider(ctx.conn, sec, tracked, ctx.today)
        quarters, xml_filings = len(insider.loaded), insider.xml_filings
    except Exception as exc:  # a quarter that will not load must not hide the rest of the step
        log.exception("insider sync failed")
        res.failed.append(("insider", _err(exc)))
        res.phase_failed = True

    # Late bookkeeping: its failure must not discard the per-company failures collected above.
    try:
        refresh_tickers(ctx.conn, ticker_map, ctx.today)  # SIC-based ETFs for new companies
    except Exception as exc:
        log.exception("refreshing tickers failed")
        res.attempted += 1
        res.failed.append(("refresh-tickers", _err(exc)))
        res.phase_failed = True
    try:
        latest = ctx.conn.execute("SELECT MAX(filing_date) FROM filings").fetchone()[0]
        res.latest_data_date = date.fromisoformat(latest) if latest else None
    except Exception as exc:
        log.exception("reading the latest filing date failed")
        res.attempted += 1
        res.failed.append(("latest-date", _err(exc)))
        res.phase_failed = True
    res.detail = (f"companies={len(tracked)} changed={len(changed)} retried={len(retry_now)} "
                  f"insider_quarters={quarters} form4_xml={xml_filings}")
    return res


def step_macro(ctx: UpdateContext) -> StepResult:
    result = sync_macro(ctx.conn, ctx.clients.fred, ctx.settings.fred_api_key, ctx.today)
    latest = max(result.latest.values()) if result.latest else None
    return StepResult("macro", attempted=len(MACRO_SERIES), failed=result.failed,
                      latest_data_date=date.fromisoformat(latest) if latest else None)


STEP_FUNCS: dict[str, Callable[[UpdateContext], StepResult]] = {
    "universe": step_universe, "prices": step_prices, "edgar": step_edgar, "macro": step_macro,
}


def _record(ctx: UpdateContext, res: StepResult) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    upsert_rows(ctx.conn, "fetch_failures", ["run_id", "step", "item", "error", "at"],
                [(ctx.run_id, res.name, item, err, now) for item, err in res.failed])
    if res.healthy:
        upsert_rows(ctx.conn, "provider_status",
                    ["provider", "last_success", "latest_data_date", "detail"],
                    [(res.name, now, res.latest_data_date.isoformat() if res.latest_data_date
                      else None, res.detail)])


def run_update(ctx: UpdateContext, steps: Sequence[str] = STEPS,
               funcs: dict[str, Callable[[UpdateContext], StepResult]] | None = None
               ) -> list[StepResult]:
    funcs = funcs or STEP_FUNCS
    results = []
    for name in steps:
        log.info("step %s: start", name)
        try:
            res = funcs[name](ctx)
        except Exception as exc:  # the whole step failed, e.g. a source is down
            log.exception("step %s failed", name)
            res = StepResult(name, attempted=1, failed=[("*", _err(exc))], phase_failed=True)
        _record(ctx, res)
        log.info("step %s: %d/%d failed", name, len(res.failed), res.attempted)
        results.append(res)
    return results


def build_context(settings: Settings, rules: Rules, today: date,
                  tickers: set[str] | None) -> UpdateContext:
    conn = connect(settings.db_path)
    migrate(conn)
    clients = make_clients(settings)
    return UpdateContext(conn=conn, settings=settings, rules=rules, clients=clients,
                         price_providers=[YFinancePrices(), NasdaqPrices(clients.nasdaq)],
                         today=today, run_id=uuid4().hex[:12], tickers=tickers)
