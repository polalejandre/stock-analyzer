# Viability spike (2026-09-29) — THROWAWAY

This is exploratory code. It answered one question: *can free data sources support a fundamentals-first "dislocation" scanner, and does the setup show any edge?* It is **not** part of the real project, and nothing should import it. The results are written up in [`docs/plans/2026-09-29-stock-analyzer-design-progress.md`](../../docs/plans/2026-09-29-stock-analyzer-design-progress.md#5-viability-spike-2026-09-29-results).

## Run

```bash
uv venv .venv && uv pip install --python .venv/bin/python pandas numpy requests yfinance
export SEC_USER_AGENT="StockAnalyzerSpike you@example.com"   # SEC requires a contact email
.venv/bin/python step1_coverage.py      # EDGAR fundamentals coverage
.venv/bin/python step2_prices.py        # yfinance prices + Stooq fallback test
.venv/bin/python step3_events.py        # 8-K Item 2.02 + Form 4 insider buys
.venv/bin/python step4_dislocation.py   # historical recovery study + today's scan
.venv/bin/python step5_chart.py         # writes preview.html (lightweight-charts)
```

The scripts cache raw responses in `cache/`, which is git-ignored.

## Files

| File | Purpose |
|---|---|
| `common.py` | The ticker universe, the sector ETF map, and cached SEC GET helpers |
| `fundamentals.py` | Point-in-time quarterly series from XBRL companyfacts: derives discrete quarters from YTD differences, and computes TTM and YoY |
| `step1_coverage.py` | Coverage report per ticker and concept |
| `step2_prices.py` | Fetches OHLCV from 2016 onward and probes Stooq |
| `step3_events.py` | 8-K earnings releases and Form 4 open-market purchases (code `P`) |
| `step4_dislocation.py` | The dislocation rule, forward returns vs SPY, and a breakdown by fundamentals, context and divergence |
| `step5_chart.py` + `preview_template.html` | Static preview of the setup chart for architecture B |
