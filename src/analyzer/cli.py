"""Command-line entry point: `analyzer ...`."""

import logging
from datetime import date

import typer

from analyzer.config import ConfigError, load_rules, load_settings
from analyzer.pipeline.update import FAILURE_LIMIT, STEPS, build_context, run_update

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
    if not value:
        return None
    return {t.strip().upper().replace(".", "-") for t in value.split(",") if t.strip()}


@app.command()
def update(
    step: list[str] = typer.Option(None, "--step", help=f"Run only these steps: {', '.join(STEPS)}"),
    tickers: str = typer.Option(None, help="Comma-separated subset for a quick run."),
    today: str = typer.Option(None, help="Override today's date (YYYY-MM-DD)."),
) -> None:
    """Fetch and store the latest data for the whole universe."""
    steps = step or list(STEPS)
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        typer.echo(f"Unknown step(s): {', '.join(unknown)}. Choose from: {', '.join(STEPS)}")
        raise typer.Exit(2)
    try:
        run_day = date.fromisoformat(today) if today else date.today()
    except ValueError as exc:
        typer.echo(f"Invalid --today {today!r}: expected YYYY-MM-DD")
        raise typer.Exit(2) from exc
    try:
        settings = load_settings()
        rules = load_rules()
    except ConfigError as exc:
        typer.echo(f"Configuration error: {exc}")
        raise typer.Exit(2) from exc
    ctx = build_context(settings, rules, run_day, _parse_tickers(tickers))
    results = run_update(ctx, steps)
    for r in results:
        latest = r.latest_data_date.isoformat() if r.latest_data_date else "-"
        typer.echo(f"{r.name:9} attempted={r.attempted:<6} failed={len(r.failed):<5} "
                   f"({r.failure_rate:.1%}) latest={latest} {r.detail}")
        for item, err in r.failed[:10]:
            typer.echo(f"  ! {item}: {err}")
        if len(r.failed) > 10:
            typer.echo(f"  ... {len(r.failed) - 10} more in table fetch_failures")
    if any(r.failure_rate > FAILURE_LIMIT for r in results):
        raise typer.Exit(1)
