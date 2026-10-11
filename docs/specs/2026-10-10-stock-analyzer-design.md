# Stock Analyzer — Design Spec

- **Date:** 2026-10-10
- **Status:** Approved section by section in brainstorming on 2026-10-10. Awaiting owner review of this written spec.
- **History:** Decisions, the viability spike and how each choice evolved are in [`docs/plans/2026-09-29-stock-analyzer-design-progress.md`](../plans/2026-09-29-stock-analyzer-design-progress.md). This spec states only the final design.

> The tool is a decision aid, not financial advice. The owner makes every trade.

---

## 1. Purpose

A **personal** tool (not a product) that scans the US market every day. It surfaces **undervalued quality companies whose stock has dislocated**, i.e. fallen because of a correction or an overreaction while the business still performs, and that can be expected to recover at least partly.

Every morning a local dashboard shows a short, ranked list of these setups. Each setup explains why the stock is cheap, why the company is quality, why it dropped, and gives a trade plan. Claude Code writes the qualitative analysis; the engine produces every number.

The main focus is **finding the right stocks**. The entry / stop / target rules in Section 8 are a first version and are expected to change.

### Success criteria

1. Every morning the dashboard shows a short, ranked list of setups with reasons, plans and Claude's analysis.
2. The research harness shows, on point-in-time out-of-sample data, that gated setups beat both SPY and ungated dislocated stocks (Section 9.6).
3. Logged positions get exit alerts on the dashboard.

### Non-goals (v1)

- Accounts, multi-user, hosting for others.
- Paid data.
- Broker integration, order placement, position sizing.
- Intraday data or intraday stops. Everything runs on the daily close.
- Editing from the dashboard (it is read-only in v1).
- Push notifications.
- Calling the Claude API from the engine.
- CNN Fear & Greed or other unofficial sentiment feeds.

---

## 2. Universe

The universe on any date is the union of:

1. **S&P 500 and S&P 400 members** on that date. Current membership and past membership come from the Wikipedia lists and their changes tables, so research is not limited to today's survivors.
2. **Young listings:** US-listed common stocks whose first trade date (from price history) is less than 5 years before that date and whose market cap is ≥ $2B. Candidates come from SEC `company_tickers_exchange.json`. Example target: SNDK after its spin-off.

The whole universe is then filtered to **20-day average dollar volume ≥ $25M**, so every setup is tradeable.

"Young listing" (universe membership, listed < 5 years and outside the indexes) is distinct from the "young" scoring variant (fewer than 3 years of own fundamentals history, Section 7.4). A young listing with 3+ years of history is scored with the standard model.

---

## 3. Data

**Rule:** primary sources and structured data only. No HTML scraping of news sites.

| Need | Source | Notes |
|---|---|---|
| Fundamentals | SEC EDGAR XBRL companyfacts API | History back to about 2008, with `filed` dates for point-in-time use. Requires a `User-Agent` with a contact email. |
| Fundamentals fallback | XBRL instance document of the latest 10-Q / 10-K | Used when companyfacts lags behind the latest filing. |
| Daily prices | yfinance | Primary. |
| Daily prices fallback | Nasdaq public endpoint (`api.nasdaq.com/api/quote/{SYMBOL}/historical`) | Chosen by the 2026-10-10 probe: no key, stocks and ETFs, split-adjusted OHLCV, about 10 years. Class shares use a dot (`BRK.B`). |
| Earnings releases, guidance | EDGAR 8-K, Item 2.02 | The company's own press release. |
| Insider buying | EDGAR Form 4, transaction code `P` | Open-market purchases. |
| Industry classification | EDGAR submissions API (SIC code) | SIC maps to an industry ETF. |
| Index membership | Wikipedia S&P 500 / 400 lists and changes tables | Current and past. |
| Young-listing candidates | SEC `company_tickers_exchange.json` | |
| Macro liquidity regime | FRED: `WALCL`, `RRPONTSYD`, `M2SL`, `NFCI`, `BAMLH0A0HYM2`, `DFII10` | Free API key required. |

### 3.1 Data layer requirements

- One interface per data type: `PriceProvider`, `FundamentalsProvider`, `EventsProvider`, `MacroProvider`, `UniverseProvider`. Any implementation can be swapped without touching the rest.
- `PriceProvider` supports a **fallback chain**: if the primary fails for a symbol, the next provider is tried.
- Raw responses are cached on disk. Filings are immutable and cached forever. Prices and companyfacts refresh incrementally.
- SEC requests stay under 10 per second and send `SEC_USER_AGENT`. Network errors are retried with backoff.

### 3.2 EDGAR normalization (known quirks from the spike)

The fundamentals provider must handle:

- **Tag changes over time.** A tag-normalization map gives each concept (revenue, operating income, capex, D&A, shares, debt, cash, equity, etc.) an ordered list of XBRL tags. Example: capex moved to `PaymentsToAcquireProductiveAssets` for NVDA and HD.
- **CIK changes.** A company may move to a new CIK after a reorganization (XOM). Old and new histories are merged.
- **Non-standard fiscal calendars.** 12/16-week quarters (COST, PEP) and 52/53-week years. Discrete quarters are derived from year-to-date differences, as in the spike's `fundamentals.py`.
- **Banks and insurers** have no operating income or capex. They use the financials variant (Section 7.4).
- **Companyfacts lag** (V, KO, PLD, PYPL were missing Q2 2026 in the spike). Use the instance-document fallback. If that also fails, tag the ticker "fundamentals pending latest 10-Q".

### 3.3 Point-in-time rules

- A fundamental value becomes known on its EDGAR `filed` date, never its period-end date.
- Market cap on a date = close × shares outstanding from the latest filing known on that date (`dei:EntityCommonStockSharesOutstanding`, falling back to diluted weighted-average shares).
- Enterprise value = market cap + total debt − cash, using the latest known balance sheet.
- Returns use adjusted close, so dividends and splits are included.

---

## 4. Architecture

```
      bin/daily.sh (systemd timer, 07:00 Europe/Madrid, Tue–Sat)
        │  analyzer update ─▶ analyzer scan ─▶ claude -p "/daily-report"
        ▼
┌───────────────┐   ┌───────────┐   ┌───────────┐   ┌──────────┐   ┌─────────────────────────┐
│ data/         │──▶│ features/ │──▶│ signals/  │──▶│ store/   │──▶│ report/ snapshot export │
│ universe/     │   │ value     │   │ gates     │   │ SQLite   │   │ reports/<date>/*.json   │
│ providers +   │   │ quality   │   │ score     │   └──────────┘   └───────────┬─────────────┘
│ disk cache    │   │ disloc.   │   │ plans     │                              │
└───────────────┘   │ flow      │   │ exits     │                ┌─────────────▼─────────────┐
                    │ technical │   │ shorts    │                │ Claude Code /daily-report │
                    │ regime    │   └───────────┘                │ writes analysis.json      │
                    └───────────┘                                └─────────────┬─────────────┘
      research/ reuses features/ + signals/ on point-in-time data              │
                                                                ┌──────────────▼────────────┐
                                                                │ web/ static dashboard     │
                                                                │ React + lightweight-charts│
                                                                │ served by analyzer serve  │
                                                                └───────────────────────────┘
```

### 4.1 Repository layout

```
pyproject.toml
rules.toml                      # all thresholds and weights, versioned in git
bin/daily.sh
src/analyzer/
  cli.py                        # Typer entry point
  config.py                     # .env + rules.toml loading
  universe/                     # index membership (current + past), young listings, liquidity filter
  data/                         # provider interfaces, implementations, disk cache, EDGAR normalization
  features/                     # pure functions: value, quality, dislocation, flow, technical, regime
  signals/                      # gates, score, tiers, plans, exits, shorts, watchlist
  research/                     # event study and backtest harness
  store/                        # sqlite3 access, numbered SQL migrations
  report/                       # snapshot and per-ticker JSON export, JSON schemas
web/                            # React + Vite + TypeScript + lightweight-charts (static build)
.claude/skills/
  daily-report/
  ticker/
  research-report/
tests/
data/                           # gitignored: SQLite DB, raw cache
reports/                        # gitignored: daily output
docs/research/oos-runs.csv      # committed log of out-of-sample runs
```

### 4.2 Unit boundaries

- `features/` functions are **pure**: data frames in, numbers out, with an explicit `as_of` date. They never fetch data.
- `signals/` consumes features and `rules.toml` only. Every decision it makes produces a human-readable reason string.
- `research/` calls the same `features/` and `signals/` code as the live scan, so research and live results cannot drift apart.
- `report/` is the only producer of the files the dashboard reads. Their shape is fixed by JSON schemas in `report/`.
- `web/` reads files only. It has no knowledge of SQLite or Python.

---

## 5. Features

All features are computed as of a date, from point-in-time data. All thresholds below are **starting values** stored in `rules.toml`; the research harness tunes them on the in-sample period only.

### 5.1 Value

- Metrics: P/E, EV/EBITDA (EBITDA = TTM operating income + D&A), FCF yield (TTM operating cash flow − capex, over market cap), P/S.
- A metric is dropped when its denominator is ≤ 0.
- **Own-history percentile:** each metric's percentile vs the stock's own daily history over the last 5 years. At least 3 years of history are required; otherwise the young variant applies (Section 7.4).
- **Peer comparison:** each metric vs the median of its **peer group**: stocks in the universe with the same industry (SIC mapping) when the group has ≥ 8 members, otherwise the same sector.

### 5.2 Quality

- TTM revenue and EPS growth YoY, and the last quarter's growth YoY.
- Operating margin trend: TTM vs one year earlier.
- TTM FCF and its trend.
- Net debt / EBITDA.
- Guidance tone from the latest 8-K Item 2.02 text: keyword heuristic for "raises", "lowers / cuts", "reaffirms" guidance. Labeled low-confidence. Deterministic, so backtests are reproducible.
- **Insider cluster buying:** during the drawdown window, ≥ 2 distinct insiders with Form 4 code `P`, or a CEO/CFO purchase ≥ $100k.
- Share-count trend (dilution), used for young listings.

### 5.3 Dislocation

- Drawdown from the 63-day high.
- Gap vs the reference ETF over 63 days. The reference ETF is the industry ETF when the SIC code maps to one, otherwise the sector SPDR ETF.
- **Drop shape:** "shock" if any single-day close-to-close drop ≥ 7% occurred in the drawdown window, otherwise "drift".
- **Context:** "market correction" if SPY is ≥ 8% below its 63-day high during the window, otherwise "idiosyncratic".
- For young listings, the drawdown from the high since listing is also reported.

### 5.4 Flow (money going into an industry)

- **Industry relative strength:** the reference ETF's return vs SPY over 63 and 126 days.
- **Industry dollar-volume trend:** 20-day average dollar volume ÷ 120-day average, for the reference ETF.
- **Industry flow tag:** "hot" when the industry ranks in the top 20% of industries by relative strength and its dollar-volume trend is > 1.2; "cold" when it ranks in the bottom 20%; otherwise "neutral".
- **Stock accumulation during the dip:** ratio of average volume on up days vs down days in the drawdown window, and on-balance-volume divergence (OBV making a higher low while price makes a lower low).

### 5.5 Technical

- RSI(14) divergence, bullish and bearish, on the daily and weekly charts.
- ATR(14).
- Recent swing low / high, and whether a recent support level has been reclaimed.
- 50-day moving average.

### 5.6 Liquidity regime (context only)

Six FRED indicators are each classed as loose or tight: NFCI below 0; HY spread below its 3-year median; Fed balance sheet (`WALCL`) 13-week change > 0; M2 YoY > 0; reverse repo (`RRPONTSYD`) falling over 13 weeks; real 10-year yield below its 3-year median. **Loose** when ≥ 4 are loose, **tight** when ≤ 2 are loose, otherwise **neutral**.

The regime is shown on the dashboard and broken out in research. It does not affect the score unless research shows that the edge really differs by regime.

---

## 6. Signal types

| Type | Meaning |
|---|---|
| Setup (long) | Passes all buy gates (Section 7.1). Ranked by score. |
| Watchlist | Passes the buy gates except the drop shape, because the drop included a shock day (Section 7.2). |
| Short | Passes the short gates (Section 7.5). |
| Position alert | An exit rule fired on a logged position (Section 8.3). |

---

## 7. Scoring model

**Gates first, then a score.** Gates decide whether a stock is a setup at all. The score only ranks the setups that pass, so a strong chart can never make an expensive stock appear.

### 7.1 Buy gates (all must pass)

1. **Undervalued:** median own-history percentile across the available value metrics ≤ 30, **and** cheaper than the peer median on ≥ 2 metrics.
2. **Dislocated:** drawdown from the 63-day high ≥ 15%, **and** ≥ 10% worse than the reference ETF over 63 days.
3. **Not a value trap.** Rejected if any of these hold:
   - TTM revenue YoY < 0 and quarterly revenue has fallen YoY in each of the last 2 quarters;
   - TTM operating margin down > 5 percentage points YoY;
   - TTM FCF < 0 and lower than one year earlier;
   - net debt / EBITDA > 4.0;
   - the latest 8-K guidance tone is "lowers / cuts".
4. **Drop shape is drift, not shock.** Shock drops go to the watchlist instead.
5. **Plan available:** at least one plan in Section 8.1 passes the reward/risk gate.

### 7.2 Watchlist (shock drops)

A stock that passes gates 1, 2, 3 and 5 but has a shock drop goes to the watchlist. It is promoted to a setup once it **stabilizes**: 10 consecutive trading days without a new closing low, while still passing the other gates. A promoted setup keeps a "shock drop" flag, and the daily report must explain the 8-K behind the drop.

The spike showed shock drops on "good numbers" behave like a coin flip, because the market is usually pricing news that trailing figures do not show yet.

### 7.3 Score (0–100, ranking only)

Each component is normalized to 0–1, then weighted:

| Component | Weight | Normalization (starting values) |
|---|---|---|
| Value depth | 30 | (30 − median own-history percentile) / 30, blended equally with the share of metrics cheaper than the peer median |
| Quality | 30 | Average of sub-scores: growth, margin trend, FCF, balance sheet, guidance tone |
| Dislocation type | 15 | Correction + drift = 1.0; idiosyncratic + drift = 0.7; promoted shock = 0.4 |
| Flow | 15 | Industry flow (hot 1.0, neutral 0.5, cold 0.2), blended equally with stock accumulation |
| Technical confirmation | 10 | Bullish RSI divergence (daily or weekly) and / or reclaimed support |
| Insider cluster buying | +5 bonus | Capped so the total stays ≤ 100 |

The weights are **fixed, not fitted**. Research checks that higher scores rank forward returns in order; fitting the weights on the same data would overfit.

**Tiers:** A ≥ 75, B ≥ 60, otherwise C.

### 7.4 Variants

| Variant | Applies to | Value | Quality | Other |
|---|---|---|---|---|
| Standard | Everything not below | Section 5.1 | Section 5.2 | |
| Financials | Banks and insurers (by SIC) | P/E and P/B | ROE trend and EPS growth | No EV/EBITDA, FCF or net-debt cap |
| REITs | REITs (by SIC) | P/FFO, with FFO ≈ net income + depreciation | Section 5.2 without the FCF rule | |
| Young | < 3 years of own fundamentals history | Peer-relative only, growth-adjusted (e.g. EV/Sales relative to revenue growth vs peers); the undervalued gate becomes "cheaper than the peer median on ≥ 2 metrics, including the growth-adjusted one" | Revenue growth trend, gross margin trend, FCF or cash runway | Extra value-trap rule: share count up > 10% YoY. Labeled "young — peer-relative valuation, lower confidence". |

### 7.5 Shorts (mirror image)

All must pass:

1. **Overvalued:** median own-history percentile ≥ 70, and more expensive than the peer median on ≥ 2 metrics.
2. **Deteriorating**, at least one of: revenue growth slowing for 2 quarters in a row; TTM operating margin down YoY; latest 8-K guidance tone "lowers / cuts".
3. **Bearish confirmation (required):** bearish RSI divergence (daily or weekly), or a close below the 63-day low.

Shorts carry the flag "check borrow with broker", because borrow availability and cost are not in free data.

### 7.6 Output per signal

A score, a tier, the component scores, and an ordered list of reason strings. Each feature that fires adds one reason, for example "EV/EBITDA at 12th percentile of its 5y range" or "Industry hot: semis, dollar volume 1.8× normal, RS +22% vs SPY".

---

## 8. Plans, positions and exits

First version, expected to change.

### 8.1 Plans

Every long setup gets a swing plan and a position plan. The owner picks one when logging a position.

| | Swing (1–4 weeks) | Position (1–6 months) |
|---|---|---|
| Entry zone | Last close ± 0.5 × ATR(14); act at the next open | Same |
| Stop | 2 × ATR(14) below the recent swing low | 3 × ATR(14) below the recent swing low, plus the thesis stop |
| Target | The closer of: a 50% retrace of the drop (63-day high to recent swing low), or the 50-day MA. Only levels above entry count. | The closer of: full retrace to the 63-day high, or the price at which the median value percentile returns to 50. Only levels above entry count. |
| Time stop | 20 trading days | 126 trading days |

- **Reward/risk gate:** a plan with (target − entry) / (entry − stop) < 1.5 is not shown. Entry is the last close.
- **Thesis stop (position plan only):** a new 10-Q or 8-K makes the stock fail the value-trap gate.

**Short plan:** entry after the bearish confirmation; stop 2 × ATR(14) above the recent swing high; target the closer of the 50-day MA or the price at which the median value percentile falls back to 50; time stop 20 trading days.

### 8.2 Positions

Logged with `analyzer position add TICKER --plan swing|position [--entry PRICE] [--date DATE]`, closed with `analyzer position close`, or by asking Claude, which runs the same commands. Stored in SQLite.

### 8.3 Exit alerts (evaluated on each scan, on the daily close)

1. Stop hit: close below the stop (above it, for shorts).
2. Target hit.
3. Breakeven: once the trade is up ≥ 1R, suggest moving the stop to entry.
4. Time stop: the plan's horizon expired.
5. Thesis broken: the value-trap gate fails, or the latest 8-K guidance tone is "lowers / cuts". For shorts: a guidance raise or a strong earnings beat.
6. Valuation normalized: median value percentile ≥ 50 (position plan).

### 8.4 Paper tracking

Every setup and short is paper-tracked automatically with the same plans and exit rules, whether or not it is logged as a position. Each tracked signal stores the `rules.toml` version that produced it. This is the live track record.

---

## 9. Research harness

### 9.1 Scope

Event study and backtest over the point-in-time universe (Section 2), using the same `features/` and `signals/` code as the live scan, with the point-in-time rules of Section 3.3.

### 9.2 Period and split

- Events start around 2014, because valuation percentiles need at least 3 years of history.
- **In-sample:** 2014–2021. Thresholds are tuned here only.
- **Out-of-sample:** 2022 to today.
- Every out-of-sample run is appended to `docs/research/oos-runs.csv` (date, rule version, headline metrics), so repeated peeking is visible.

### 9.3 Survivorship

yfinance often has no prices for delisted tickers. Each run reports price coverage of past index members and of past young listings, so the remaining survivorship bias is visible.

### 9.4 Metrics per event

- Forward returns at 5, 21, 63 and 126 trading days: raw, and in excess of SPY and of the reference ETF.
- Win rate, median return, max adverse excursion.
- Plan outcomes from Section 8.1: target, stop or time stop.
- Breakdowns by drop shape, context, sector / industry, flow tag, liquidity regime, variant and tier.
- Score quintiles vs forward returns.
- **Young listings and shorts are reported as separate buckets** and are not part of the go/no-go.

### 9.5 Comparison groups and overlap

- Groups: (1) SPY; (2) all dislocated stocks (gate 2 only); (3) gated setups. If group 3 does not beat group 2, the fundamentals layer is not earning its place.
- A 42-trading-day cooldown per ticker between events.
- Confidence intervals by bootstrap resampling in month blocks, because events cluster.

### 9.6 Go/no-go (fixed in advance)

On the out-of-sample period, gated setups (group 3, excluding young listings) must show all of:

- n ≥ 100;
- median 63-day excess return vs SPY > 0;
- win rate ≥ 55% at 63 days;
- better median 63-day excess return than group 2.

Score quintiles must also rank 63-day forward returns roughly in order (top quintile above bottom quintile).

**If it fails,** no dashboard work starts. The owner decides whether to revise the rules (on in-sample data only) or stop.

### 9.7 Outputs

Results are written as CSV and JSON under `data/research/<run-id>/`. The dashboard's track-record tab shows the latest run's headline numbers next to live paper-tracking results. The `/research-report` skill writes an analysis JSON of the run that the same tab displays.

---

## 10. Reports and dashboard

**The dashboard is where the report is read.** The engine produces every number; Claude Code writes the analysis; a static dashboard shows both.

### 10.1 CLI (`analyzer`)

| Command | What it does |
|---|---|
| `update` | Incremental fetch: universe, prices, EDGAR, FRED |
| `scan [--date D]` | Computes features and signals, evaluates position alerts and paper tracking, writes SQLite and the report files below |
| `explain TICKER` | JSON with every gate's pass/fail, every metric and every reason, whether flagged or not |
| `position add / close / list` | Position log |
| `research run` | Runs the research harness |
| `serve` | Serves `web/dist` and `reports/` on `localhost` |

### 10.2 Files written by `scan`

```
reports/
  index.json                      # list of available report dates
  <YYYY-MM-DD>/
    snapshot.json                 # written by scan
    tickers/<TICKER>.json         # chart data per flagged ticker and per position
    analysis.json                 # written by /daily-report
```

- **`snapshot.json`:** run metadata (date, rule version, data freshness per source, liquidity regime, hot and cold industries, per-ticker failures); `setups[]` (score, components, tier, reasons, valuation percentiles, quality metrics, drop shape and context, flow tag, plans, flags such as young / financial / REIT / shock-promoted / fundamentals-pending, latest 8-K URL); `watchlist[]`; `shorts[]`; `positions[]` with alerts; live track-record summary.
- **`tickers/<TICKER>.json`:** about 2 years of daily OHLCV, RSI series, divergence line coordinates, plan price levels.
- **`analysis.json`:** a market summary, and per ticker: thesis, drop explanation, 8-K takeaways (with URL), risks / what would break the thesis.

JSON schemas for all three live in `src/analyzer/report/` and are tested.

### 10.3 Claude Code skills (`.claude/skills/`)

- **`/daily-report`:** runs `analyzer update` and `analyzer scan` only if today's `snapshot.json` does not exist yet (the timer script already runs them); reads `snapshot.json`; reads the latest 8-K press release for the top ~10 setups, every promoted shock setup and every ticker with a position alert; writes `analysis.json`. If the run is stale or failed, it writes an analysis that says so instead of analyzing.
- **`/ticker TICKER`:** on-demand deep dive using `analyzer explain`. Answers "why is X flagged?" and "why isn't X flagged?" in the conversation.
- **`/research-report`:** reads the latest research run and writes its analysis JSON.

**Guardrail:** the skill instructions require every number Claude writes to come from the engine's JSON. Claude adds only qualitative reading of filings, with links. No invented figures.

### 10.4 Dashboard screens

Static, read-only web app: React + Vite + TypeScript + lightweight-charts, built into `web/dist`.

- **Today:**
  1. Header: data freshness per source, liquidity regime, hot and cold industries, Claude's market summary, scan failures if any.
  2. Position alerts (first, because they are time-sensitive).
  3. Ranked setup cards: why cheap, why quality, why it dropped (drop shape and context plus Claude's 8-K reading), flow tag, plans, risks.
- **Ticker page:** interactive chart (candles, RSI pane, divergence lines, entry / stop / target lines) next to the valuation and quality numbers and Claude's analysis.
- **Tabs:** young listings, watchlist (shock drops, with what to check), shorts, positions, track record (live paper results and latest research run).
- **History:** any past date from `reports/index.json`.

If `analysis.json` is missing for a date, the dashboard still shows the engine's numbers and reasons and notes that the analysis is missing.

---

## 11. Operations

### 11.1 Scheduling

- A systemd **user** timer runs `bin/daily.sh` at 07:00 Europe/Madrid, Tuesday to Saturday (01:00 ET), so after-hours 8-Ks and EDGAR's overnight processing are included.
- `Persistent=true` runs a missed job at the next boot.
- `bin/daily.sh` runs `analyzer update`, `analyzer scan`, then `claude -p "/daily-report"`. It exits early on non-trading days (no new SPY bar).

### 11.2 Freshness and failure handling

- Each provider records its last successful fetch and the latest date in its data. Both appear in `snapshot.json` and on the dashboard header.
- If prices are stale by more than 1 trading day, `scan` aborts with a clear error and writes no report.
- A failure for one ticker does not stop the scan. It is logged and listed in `snapshot.json`.
- If more than 5% of tickers fail, `scan` fails.

### 11.3 Storage and configuration

- `data/` (gitignored): the SQLite database (WAL mode) and the raw cache.
- `reports/` (gitignored).
- `.env`: `SEC_USER_AGENT`, `FRED_API_KEY`.
- `rules.toml` (committed): every threshold and weight in this spec, plus a `version` field. Changing any value requires bumping the version.

### 11.4 Tooling

Python 3.12 with uv, pandas, stdlib `sqlite3` with numbered SQL migrations, Typer, ruff, pytest. Web: Node, Vite, React, TypeScript, lightweight-charts.

---

## 12. Testing

- **Features:** unit tests for the pure functions in `features/`, with small fixtures.
- **EDGAR quirks:** recorded fixtures for the NVDA capex tag change, the XOM CIK merge, COST's 16-week quarters, a bank, and a companyfacts lag case.
- **No look-ahead:** features computed as of date D ignore every filing filed after D and every price after D.
- **Gates and score:** table-driven tests per gate, per variant, and for reason strings.
- **Report files:** schema tests for `snapshot.json`, `tickers/*.json` and `analysis.json`.
- **Research sanity:** random entries show roughly 0 excess return vs SPY. If not, the harness has a bug.
- **Network:** default tests never touch the network. `pytest -m live` runs opt-in smoke tests against the real APIs.
- **Dashboard:** renders from a fixture `reports/` directory, including the missing-`analysis.json` case.

---

## 13. Build phases

Each phase gets its own implementation plan.

1. **Data layer and universe.** Starts with the price-fallback probe (Tiingo free tier, Nasdaq public endpoint). Ends with `analyzer update` filling SQLite and the cache for the full universe, with the EDGAR quirks handled.
2. **Features, signals and research harness.** Ends with the go/no-go decision of Section 9.6. Thresholds are tuned on in-sample data only.
3. **Report and dashboard,** built together: `scan` output files, position CLI, `explain`, the three skills, the static dashboard, `serve`, `bin/daily.sh` and the systemd timer. Starts only after a "go".
4. **Optional, later:** FastAPI backend for logging positions and triggering scans from the dashboard, then Docker and a VPS.

---

## 14. Open questions (settled during implementation)

- Price fallback: Nasdaq public endpoint (decided 2026-10-10).
- Final thresholds: tuned by the research harness on in-sample data.
- How much weight insider buying and guidance tone deserve inside the quality score: checked in research breakdowns; weights stay fixed unless a rule-version change is made deliberately.
