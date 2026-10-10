"""Command-line entry point: `analyzer ...`."""

import logging
import time
from datetime import date

import typer

from analyzer.config import ConfigError, load_rules, load_settings
from analyzer.data.clients import make_clients
from analyzer.data.edgar.companies import load_ticker_map
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.http import FetchError, NotFound
from analyzer.pipeline.audit import actionable, missing_reference_etfs, summarize
from analyzer.pipeline.audit import audit as run_audit
from analyzer.pipeline.update import STEPS, build_context, run_update
from analyzer.store.db import connect, migrate
from analyzer.universe.members import price_symbols

app = typer.Typer(help="Stock Analyzer engine.", no_args_is_help=True)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    """Stock Analyzer engine."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # urllib3 logs full query strings at DEBUG; FRED's api_key is in one.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _parse_tickers(value: str | None) -> set[str] | None:
    if value is None:
        return None
    return {t.strip().upper().replace(".", "-") for t in value.split(",") if t.strip()}


def _fail(message: str, code: int = 2):
    typer.echo(message, err=True)
    return typer.Exit(code)


@app.command()
def update(
    step: list[str] = typer.Option(None, "--step", help=f"Run only these steps: {', '.join(STEPS)}"),
    tickers: str = typer.Option(None, help="Comma-separated subset for a quick run."),
    today: str = typer.Option(None, help="Override today's date (YYYY-MM-DD)."),
) -> None:
    """Fetch and store the latest data for the whole universe."""
    unknown = [s for s in step or () if s not in STEPS]
    if unknown:
        raise _fail(f"Unknown step(s): {', '.join(unknown)}. Choose from: {', '.join(STEPS)}")
    steps = [s for s in STEPS if not step or s in step]  # canonical order, each once
    try:
        run_day = date.fromisoformat(today) if today else date.today()
    except ValueError as exc:
        raise _fail(f"Invalid --today {today!r}: expected YYYY-MM-DD") from exc
    subset = _parse_tickers(tickers)
    if subset is not None and not subset:
        raise _fail(f"--tickers {tickers!r} names no tickers")
    try:
        settings = load_settings()
        rules = load_rules()
    except Exception as exc:  # a missing key or a malformed rules.toml is a configuration problem
        reason = str(exc) if isinstance(exc, ConfigError) else f"{type(exc).__name__}: {exc}"
        raise _fail(f"Configuration error: {reason}") from exc
    if "macro" in steps and not settings.fred_api_key:
        raise _fail("Configuration error: FRED_API_KEY is not set (the macro step needs it). Get a "
                    "free key at https://fred.stlouisfed.org/docs/api/api_key.html and add it to .env")
    ctx = build_context(settings, rules, run_day, subset)
    results = run_update(ctx, steps)
    for r in results:
        latest = r.latest_data_date.isoformat() if r.latest_data_date else "-"
        typer.echo(f"{r.name:9} attempted={r.attempted:<6} failed={len(r.failed):<5} "
                   f"({r.failure_rate:.1%}) latest={latest} {r.detail}")
        for item, err in r.failed[:10]:
            typer.echo(f"  ! {item}: {err}", err=True)
        if len(r.failed) > 10:
            typer.echo(f"  ... {len(r.failed) - 10} more in table fetch_failures", err=True)
    unhealthy = [r for r in results if not r.healthy]
    for r in unhealthy:
        typer.echo(f"FAILED: {r.name} (provider_status not updated)", err=True)
    if unhealthy:
        raise typer.Exit(1)


@app.command()
def audit(
    tickers: str = typer.Option(None, help="Comma-separated subset."),
    show_all: bool = typer.Option(False, "--all", help="List every ticker, not only flagged active ones."),
    today: str = typer.Option(None, help="Override today's date (YYYY-MM-DD)."),
) -> None:
    """Report data coverage and problems per ticker."""
    try:
        run_day = date.fromisoformat(today) if today else date.today()
    except ValueError as exc:
        raise _fail(f"Invalid --today {today!r}: expected YYYY-MM-DD") from exc
    subset = _parse_tickers(tickers)
    if subset is not None and not subset:
        raise _fail(f"--tickers {tickers!r} names no tickers")
    try:
        settings = load_settings()
        rules = load_rules()
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ConfigError) else f"{type(exc).__name__}: {exc}"
        raise _fail(f"Configuration error: {reason}") from exc
    if not settings.db_path.exists():
        raise _fail("no database yet \u2014 run `analyzer update` first")
    started = time.monotonic()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        try:
            ticker_map = load_ticker_map(make_clients(settings).sec)
        except (FetchError, NotFound, OSError) as exc:  # offline or SEC down
            typer.echo(f"warning: CIK merges not loaded ({type(exc).__name__}: {exc}); "
                       "auditing without them", err=True)
            merges = None
        else:
            try:
                merges = load_cik_merges(ticker_map)
            except Exception as exc:  # malformed cik_merges.toml etc. is a real problem
                raise _fail(f"Configuration error: cik merges: {type(exc).__name__}: {exc}") from exc
        active = set(price_symbols(conn, run_day, rules.universe)[0])
        audits = run_audit(
            conn, run_day, merges, subset, active=active,
            progress=lambda n, total: typer.echo(f"audited {n}/{total}\u2026", err=True))
        missing_etfs = missing_reference_etfs(conn)
    finally:
        conn.close()
    for key, value in summarize(audits, missing_etfs).items():
        typer.echo(f"{key:30} {value}")
    if missing_etfs:
        typer.echo(f"reference ETFs without prices: {', '.join(missing_etfs)}")
    typer.echo("")
    for a in audits:
        if show_all or actionable(a):
            marker = "" if a.active else "inactive "
            typer.echo(f"{a.ticker:8} cik={a.cik if a.cik is not None else '-':<9} "
                       f"prices={a.price_first or '-'}..{a.price_last or '-'} "
                       f"rev_q={a.revenue_quarters:<3} etf={a.sector_etf or '-'}/{a.industry_etf or '-'} "
                       f"{marker}{'; '.join(a.flags)}".rstrip())
    typer.echo(f"audited {len(audits)} tickers in {time.monotonic() - started:.1f}s", err=True)
