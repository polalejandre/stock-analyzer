# Stock Analyzer

A personal daily market scanner for US large and mid caps (S&P 500 + S&P 400). It looks for **undervalued, fundamentally strong companies whose stock has dislocated**, i.e. fallen because of a correction or an overreaction while the business still performs. It then shows the setup on a web dashboard: why the stock is cheap, why the company is quality, the entry trigger, and entry / stop / target.

It uses free, primary data sources only (SEC EDGAR, FRED, yfinance), and it is fundamentals-first. Technicals such as RSI divergences only time the entry.

> Decision aid, not financial advice.

## Status

The design is in progress. No product code exists yet.

- Design progress and decisions: [`docs/plans/2026-09-29-stock-analyzer-design-progress.md`](docs/plans/2026-09-29-stock-analyzer-design-progress.md)
- Viability spike (throwaway): [`spikes/2026-09-29-viability/`](spikes/2026-09-29-viability/)

## Planned architecture

A Python engine (data → features → signals → SQLite), a FastAPI backend, and a React + lightweight-charts frontend, run with Docker Compose.
