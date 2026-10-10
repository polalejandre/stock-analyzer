# Stock Analyzer

A personal daily market scanner for US large and mid caps (S&P 500 + S&P 400). It looks for **undervalued, fundamentally strong companies whose stock has dislocated**, i.e. fallen because of a correction or an overreaction while the business still performs. It then shows the setup on a web dashboard: why the stock is cheap, why the company is quality, the entry trigger, and entry / stop / target.

It uses free, primary data sources only (SEC EDGAR, FRED, yfinance), and it is fundamentals-first. Technicals such as RSI divergences only time the entry.

> Decision aid, not financial advice.

## Status

The design is complete and awaiting final review. No product code exists yet.

- Design spec: [`docs/specs/2026-10-10-stock-analyzer-design.md`](docs/specs/2026-10-10-stock-analyzer-design.md)
- Design history and decisions: [`docs/plans/2026-09-29-stock-analyzer-design-progress.md`](docs/plans/2026-09-29-stock-analyzer-design-progress.md)
- Viability spike (throwaway): [`spikes/2026-09-29-viability/`](spikes/2026-09-29-viability/)

## Planned architecture

A Python engine (data → features → signals → SQLite) runs each morning. Claude Code reads its output and the latest filings and writes the analysis. A local, static React + lightweight-charts dashboard shows both. A FastAPI backend and Docker are optional, later additions.
