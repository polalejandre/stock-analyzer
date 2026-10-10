# Stock Analyzer

A personal daily market scanner for US large and mid caps (S&P 500 + S&P 400). It looks for **undervalued, fundamentally strong companies whose stock has dislocated**, i.e. fallen because of a correction or an overreaction while the business still performs. It then shows the setup on a web dashboard: why the stock is cheap, why the company is quality, the entry trigger, and entry / stop / target.

It uses free, primary data sources only (SEC EDGAR, FRED, yfinance), and it is fundamentals-first. Technicals such as RSI divergences only time the entry.

> Decision aid, not financial advice.

## Status

Phase 1 (data layer) is implemented. Scoring, research and the dashboard are next.

- Design spec: [`docs/specs/2026-10-10-stock-analyzer-design.md`](docs/specs/2026-10-10-stock-analyzer-design.md)
- Design history and decisions: [`docs/plans/2026-09-29-stock-analyzer-design-progress.md`](docs/plans/2026-09-29-stock-analyzer-design-progress.md)
- Viability spike (throwaway): [`spikes/2026-09-29-viability/`](spikes/2026-09-29-viability/)

## Development

```bash
uv sync
cp .env.example .env        # must sit in the repo root; set SEC_USER_AGENT (with your email) and FRED_API_KEY
uv run pytest               # unit tests, no network
uv run pytest -m live -s -v # checks against the real data sources; -s shows the diagnostic prints
uv run analyzer update      # first run backfills everything (1–2 hours)
uv run analyzer audit       # coverage and data problems

# quick runs
uv run analyzer update --step prices --tickers AAPL,MSFT
uv run analyzer audit --all                  # list every ticker, not only flagged ones
uv run analyzer audit --today 2026-10-09     # (update also accepts --today) override today's date
```

## Planned architecture

A Python engine (data → features → signals → SQLite) runs each morning. Claude Code reads its output and the latest filings and writes the analysis. A local, static React + lightweight-charts dashboard shows both. A FastAPI backend and Docker are optional, later additions.
