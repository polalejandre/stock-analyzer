# Stock Analyzer — Design Progress

- **Date:** 2026-09-29
- **Status:** Brainstorming complete. All six sections were approved on 2026-10-10. The final spec is [`docs/specs/2026-10-10-stock-analyzer-design.md`](../specs/2026-10-10-stock-analyzer-design.md). This document is kept as the decision history.

---

## 1. Goal

A **personal** tool (not a product) that scans the US market every day and shows short-to-mid-term **buy / sell / short setups** on a web dashboard. Every setup comes with a clear explanation of why it was flagged.

The core idea, in the owner's words: what has made money lately is looking at **shorter windows**. The situations to catch are ones where a quality company's stock is going down (corrections, overreactions) while its fundamentals and macro say it should be going up. Buy low and expect a recovery, even a partial one. Example: Netflix has been doing badly in price lately, but the company's stats say otherwise.

### Success criteria

- Every morning the local dashboard shows a **short, ranked list of setups**. Each setup shows why the stock is cheap, why the company is quality, the entry trigger (if any), and entry / stop / target / horizon.
- The research harness shows that the setup rules beat SPY over their holding horizon on point-in-time data.
- Logged positions get **exit alerts** on the dashboard.
- Main focus (stated 2026-10-10): surface **undervalued quality companies that can recover**. Entry / exit details are secondary and can be refined later.

---

## 2. Decisions made

| Topic | Decision |
|---|---|
| Audience | Personal tool. No accounts, billing or licensing concerns. |
| Universe | US **S&P 500 + S&P 400** (~900 stocks), **plus young listings** (listed < 5 years, market cap ≥ $2B). Whole universe filtered to 20-day average dollar volume ≥ $25M (amended 2026-10-10). |
| Horizon | **Both**: swing (1–4 weeks) and position (1–6 months). |
| Delivery | **Dashboard is the report.** The engine produces the numbers, Claude Code writes the analysis as structured JSON, and a local static dashboard displays both (decided 2026-10-10). |
| Hosting | **Local**, served with `analyzer serve`. Docker and a VPS are deferred until a backend is added. |
| Data budget | **Free only**. |
| Sell signals | **Exits** on logged positions and on tracked signals, **plus short ideas** (overvalued + deteriorating). |
| Event signals in v1 | **Yes**: 8-K earnings releases (Item 2.02) and Form 4 insider buying. |
| Architecture | **B**: Python engine + React / lightweight-charts. The v1 dashboard is a static, read-only web app reading files; FastAPI is added later only for editing from the UI. Build the engine first. |
| Role of technicals | **Secondary.** RSI divergences (daily or weekly) confirm entry timing and raise confidence. A divergence is **not required**: a setup is actionable without one. |
| Role of valuation | **Core pillar.** Every buy setup must be undervalued, i.e. cheap vs the stock's own history and vs sector peers. |
| AI reports | **Claude Code** in v1, via a project skill (e.g. `/daily-report`). No Claude API key. Claude API inside the engine is optional, later. |

The tool is a **decision aid, not financial advice**. The owner makes every trade.

---

## 3. The core setup: "dislocation"

A **long** setup is flagged when these pillars hold:

1. **Undervalued**
   - P/E, EV/EBITDA, FCF yield and P/S are cheap vs the stock's own ~5-year history and vs its sector median.
2. **Price fell recently**
   - For example, down ≥15% from the 63-day high, and ≥10% worse than its sector ETF over the same window.
3. **Fundamentals intact or improving**
   - Latest 1–2 quarters: revenue and EPS growth, stable or improving margins, positive FCF, manageable debt.
   - Earnings beats or raised guidance in the latest 8-K.
   - Insider buying during the dip is a strong plus.
   - A **value-trap filter** rejects stocks that are cheap because the business is breaking.
4. **Macro / sector context doesn't explain the drop**
   - *Idiosyncratic:* the market and sector are fine, and the stock drifted down on noise.
   - *Market correction:* quality names were sold off together with everything else.
   - *Sector-driven for a real reason* (e.g. rates hitting REITs): lower confidence.
5. **Entry timing (optional, raises confidence)**
   - Bullish RSI divergence on the daily or weekly chart, a reclaimed support level, or selling slowing down.

**Plan per setup:**
- **Target:** a partial recovery, e.g. a 38–50% retrace of the drop or back to the 50-day MA.
- **Stop:** ATR-based, below the recent low. A fixed "20-day low" stop failed in the spike on volatile names.

**Short setup (mirror image):** overvalued + fundamentals deteriorating + bearish technical confirmation.

---

## 4. Data sources

The rule is **primary sources, structured data, no HTML scraping of news sites**. Sites like CNN, Bloomberg, Reuters, MarketWatch, Seeking Alpha and Finviz were rejected for four reasons:
- The data is second-hand.
- Scrapers break when layouts change.
- Their Terms of Service forbid scraping.
- News headlines are weak signals for a multi-week thesis.

| Need | Source | Notes |
|---|---|---|
| Fundamentals | **SEC EDGAR XBRL API** (companyfacts) | Official, free, history back to about 2008, with filing dates for point-in-time backtests. Requires a `User-Agent` with a contact email. |
| Daily prices | **yfinance** (primary) | Unofficial but works. **The fallback is still to be decided**: Stooq returned HTTP 404 in the spike. |
| Earnings events / guidance | **EDGAR 8-K** (Item 2.02) | The company's own press release. |
| Insider buying | **EDGAR Form 4** (transaction code `P`) | Open-market purchases by insiders. |
| Macro regime | **FRED API** | Rates, VIX, yield curve. |
| Market sentiment | CNN Fear & Greed (optional) | Unofficial JSON endpoint. Market regime context only. Must fail gracefully. |
| Index membership | Wikipedia S&P 500 / 400 lists and changes tables | Also used to rebuild past membership, so backtests aren't limited to today's survivors. |
| Young listings | SEC `company_tickers_exchange.json` | Candidate tickers outside the indexes. "Young" = first trade date in price history < 5 years ago. |
| Industry classification | EDGAR submissions API (SIC code) | SIC maps to an industry ETF (e.g. semis to SMH/SOXX, software to IGV, biotech to XBI, regional banks to KRE). The 11 SPDR sector ETFs are the fallback. |
| Liquidity regime | FRED: `WALCL`, `RRPONTSYD`, `M2SL`, `NFCI`, `BAMLH0A0HYM2`, `DFII10` | Labels each day loose / neutral / tight. Context only. |

Reference product: [thestockanalyzer.ai](https://thestockanalyzer.ai/). Per its Terms of Service it uses Yahoo Finance data and the Anthropic API. Its methodology is purely technical (pattern matching, 43 patterns, ATR risk zones). This project differs by putting fundamentals and valuation first.

---

## 5. Viability spike (2026-09-29): results

Throwaway code lives in [`spikes/2026-09-29-viability/`](../../spikes/2026-09-29-viability/). The sample was 41 large caps across all sectors, from 2018 to today.

### 5.1 Data

| Source | Result |
|---|---|
| EDGAR fundamentals | TTM revenue YoY computable for **38/41** tickers, FCF for **28/41**. Quarterly history back to 2008. Median filing lag is **31 days** after quarter end. |
| EDGAR 8-K Item 2.02 | **40/41** tickers had ≥3 earnings releases in the last 12 months. Clean and machine-readable. |
| EDGAR Form 4 | **100%** parsed (536 filings). It found real cluster buying, e.g. NKE's CEO plus directors, PFE's CEO plus directors, and INTC's CEO. |
| yfinance | All 53 symbols worked, and the data was current to the previous close. |
| Stooq | **Broken (HTTP 404).** A different free fallback is needed. |

**EDGAR gaps to handle in the real data layer:**
- XBRL tags change over time (e.g. capex moved to `PaymentsToAcquireProductiveAssets` for NVDA and HD). This needs a tag-normalization map.
- A company can move to a **new CIK** after a reorganization (XOM). The old and new CIK histories must be merged.
- Some companies use non-standard fiscal quarters, e.g. 12/16-week quarters (COST, PEP) or 52/53-week years.
- Banks have no operating income or capex, so they need their own quality model.
- For some tickers the companyfacts API lagged behind the latest 10-Q (V, KO, PLD, PYPL were missing Q2 2026). This needs a fallback that reads the filing itself.

### 5.2 Recovery study

A **dislocation event** here means the stock was down ≥15% from its 63-day high and ≥10% worse than its sector ETF over 63 days. There was a 42-trading-day cooldown per ticker, which gave 310 events.

| Setup (fundamentals "intact") | n | Win rate, 21 days | Median return, 21 days | Win rate, 63 days |
|---|---|---|---|---|
| Idiosyncratic, drop includes a **≥7% shock day** | 85 | 47% | −0.4% | 51% |
| Idiosyncratic, **gradual drift** | 47 | 66% | +4.3% | 57% |
| **Market correction**, drift | 21 | 81% | +6.3% | 71% |
| Candidate rule (correction OR drift) | 99 | 69% | +4.4% | 63% |
| Candidate rule + RSI bullish divergence | 26 | 77% | +6.8% | 81% |

**Findings:**
1. **How the stock fell matters most.**
   - A shock-day drop on "good numbers" behaves like a coin flip. The market is usually pricing news (e.g. a guidance cut) that trailing figures don't show yet.
   - Gradual drift and market-correction drops are where the edge is.
2. **The crude fundamentals filter did not separate winners from losers.** "Intact" won 58% of the time over 21 days, "weakening" 61%. Trailing revenue and profit growth alone is not enough. The real quality score needs:
   - guidance from the 8-K
   - margin trend
   - **valuation vs history**
   - FCF
   - balance sheet
3. **RSI divergence helps as confirmation.** It is not the driver.
4. **Stops must be ATR-based.** A 20-day-low stop was 28% below entry on INTC.

**Caveats (the numbers are directional, not proof):**
- Small sample.
- Today's index members only (survivorship bias).
- 2018–2026 was mostly a bull market.
- Correction events cluster into about 6 episodes.
- Several splits were tested on the same data (overfitting risk).

The real research harness must run on the full ~900 stocks, including past index members, and must hold out an out-of-sample period.

### 5.3 Live scan on 2026-09-28 (spike rule, not advice)

- **NFLX:**
  - down 16.3% from its 63-day high and 9.6% below XLC (just misses the 10% threshold)
  - revenue +16% TTM, +13.4% last quarter, operating profit +16.7%
  - its worst day in the window was −7.3%, which puts it in the "shock" bucket: check the news behind the drop first
  - no divergence yet
- **Flagged:** DECK (the closest match: −28%, intact, daily RSI bullish divergence, no shock day), CAT, HD, INTC.

### 5.4 Architecture B preview

`spikes/2026-09-29-viability/step5_chart.py` generates a static `lightweight-charts` page showing:
- candles and an RSI pane
- divergence lines
- entry / stop / target price lines
- a setup card explaining the reasoning

This confirmed the B look and feel is achievable.

---

## 6. Design — Section 1: Architecture and components (approved 2026-10-10)

```
              ┌──────────── scheduler (daily, after US close) ────────────┐
              ▼                                                            │
  ┌─────────────────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐    │
  │ data/ providers     │──▶│ features/│──▶│ signals/ │──▶│ store/    │◀───┘
  │ prices·EDGAR·events │   │ value    │   │ setups   │   │ SQLite    │
  │ macro·universe      │   │ quality  │   │ exits    │   └─────┬─────┘
  └─────────────────────┘   │ disloc.  │   │ shorts   │         │
                            │ technical│   └──────────┘   ┌─────▼─────┐   ┌──────────────┐
                            └──────────┘                  │ api/      │──▶│ web/         │
               research/ (event study, backtest) ────────▶│ FastAPI   │   │ React + LWC  │
                                                          └───────────┘   └──────────────┘
```

### Engine (Python package)

- **`universe/`**
  - Current S&P 500 / 400 membership.
  - Past membership rebuilt from the Wikipedia changes tables, so research isn't limited to today's survivors.
- **`data/`**
  - One interface per data type: `PriceProvider`, `FundamentalsProvider`, `EventsProvider`, `MacroProvider`.
  - Implementations: yfinance, EDGAR (with tag normalization, CIK merges and fiscal-calendar handling), FRED, and Fear & Greed (optional).
  - Raw responses are cached on disk.
  - Any provider can be swapped without touching the rest.
- **`features/`**: pure functions from data to numbers.
  - **Value:** P/E, EV/EBITDA, FCF yield, P/S. Each is expressed as a percentile vs the stock's own 5-year history and vs its sector median.
  - **Quality:** growth, margin trend, FCF, balance sheet, 8-K guidance tone, insider buying.
  - **Dislocation:** drawdown, gap vs sector, shock vs drift, market context.
  - **Technical:** RSI divergence (daily and weekly), ATR, support levels.
- **`signals/`**
  - Combines features into setups: buy dislocation, short, exit.
  - Every setup has a score **and human-readable reasons**. No black box.
- **`research/`**
  - A point-in-time event study and backtest harness.
  - It reuses the same `features/` and `signals/` code, so research results and live signals cannot drift apart.
- **`store/`**
  - SQLite in WAL mode, so the API can read while the scan writes.
  - Enough for ~900 stocks.
  - Moving to Postgres later only means changing the connection string.

### Serving

- **`api/`:** FastAPI.
  - Read endpoints: setups, ticker detail, chart data, research stats.
  - Position CRUD.
- **`web/`:** React + Vite + TypeScript + lightweight-charts.

### Deployment

Docker Compose runs 3 services from one engine image: `scheduler`, `api` and `web` (static files served by nginx). It runs locally now and can move to any VPS later.

### Build order

1. Engine
2. Research validation
3. API
4. Web

### Adjustments agreed on approval (2026-10-10)

- **Scan is a CLI command.** The daily scan runs as `analyzer scan`, not as a long-running service. The `scheduler` container is a cron-style runner that calls it. The same command serves manual runs, backfills and research.
- **Price fallback slot.** `PriceProvider` supports a fallback implementation from the start. The actual fallback source is chosen in Section 6 (operations).
- **One spec, phased plans.** One spec covers sections 1–6. Implementation is split into separate plans:
  1. Data layer and universe.
  2. Features, signals and research harness, ending in a go/no-go decision on the edge.
  3. API and web.

  No UI work starts until phase 2 confirms the edge, because research results can change thresholds and scoring.

---

## 6b. Design — Section 2: Scoring model (approved 2026-10-10)

**Approach: gates first, then a score.** Gates decide whether a stock is a setup at all. The score only ranks the setups that pass. A pure weighted score was rejected because a strong chart could outweigh an expensive valuation.

### Buy gates (all must pass)

1. **Undervalued.** P/E, EV/EBITDA, FCF yield and P/S each get a point-in-time percentile vs the stock's own 5-year history. The gate passes when the median percentile is ≤ 30 and the stock is cheaper than its sector median on at least 2 metrics. A metric is dropped when its denominator is ≤ 0 (for example, P/E with negative earnings).
2. **Dislocated.** Drawdown from the 63-day high and the gap vs the sector ETF. The spike thresholds are the starting point; the research harness sets the final values.
3. **Not a value trap.** The stock is rejected if any of these hold:
   - TTM revenue is falling and has fallen for 2 quarters in a row.
   - Operating margin is down sharply YoY.
   - TTM FCF is negative and getting worse.
   - Net debt / EBITDA is above a cap.
   - The latest 8-K cuts guidance.
4. **Drop type is not a shock.** A drop that includes a ≥7% shock day goes to a **watchlist**, not the setup list. It moves to the setup list after it stabilizes (for example, N days with no new low) and the 8-K has been checked. The spike showed that shock drops behave like a coin flip.

### Score (0–100, ranking only)

| Component | Weight |
|---|---|
| Value depth | 30 |
| Quality (growth, margin trend, FCF, balance sheet, guidance tone) | 30 |
| Dislocation type (market correction ranks above drift) | 15 |
| Flow (industry money flow and accumulation during the dip; see Section 4) | 15 |
| Technical confirmation (RSI divergence, reclaimed support) | 10 |
| Insider cluster buying during the dip | Bonus |

Weights amended 2026-10-10 to add the flow component (originally value 35, quality 35, dislocation 20, technical 10).

The weights are **fixed, not fitted**. The research harness checks that higher scores rank forward returns in order (quintile monotonicity). Fitting the weights on the same data would overfit.

### Sector variants

- **Financials (banks, insurers):** value uses P/E and P/B. Quality uses ROE trend and EPS growth. There is no EV/EBITDA, no FCF and no debt cap.
- **REITs:** value uses P/FFO, with FFO approximated as net income plus depreciation.
- **Young listings (< 3 years of own history; added 2026-10-10):** the undervalued gate is peer-relative only (cheap vs sector/industry peers), growth-adjusted (for example EV/Sales relative to revenue growth vs peers). Dislocation also measures the drawdown from the high since listing. Quality uses revenue growth trend, gross margin trend and FCF or cash runway. Share dilution (a rising share count) is an extra value-trap check. The report labels these "young — peer-relative valuation, lower confidence".

### 8-K guidance tone in v1

A keyword heuristic ("raises", "lowers" or "reaffirms" guidance), labeled low-confidence. Claude API summaries come later.

### Shorts (mirror image)

- Overvalued gate: median percentile ≥ 70 and above the sector median.
- Deterioration: revenue growth slowing, margins shrinking, or a guidance cut.
- Bearish technical confirmation is **required** for shorts, because short losses are unbounded.

### Output per setup

A score, a tier (A/B/C) and a list of human-readable reasons. Each feature that fires adds one reason, for example "EV/EBITDA at 12th percentile of its 5y range".

---

## 6c. Design — Section 3: Setups, exits and delivery (approved 2026-10-10)

The owner's priority is surfacing undervalued companies that can recover. The entry / exit rules below are accepted as a first version and can change later.

### One setup, two plans

Every buy setup shows a swing plan and a position plan. The owner picks one when logging a position.

| | Swing (1–4 weeks) | Position (1–6 months) |
|---|---|---|
| Entry | Zone: last close ± 0.5 ATR(14). Act at the next open. | Same zone |
| Stop | 2 × ATR(14) below the recent swing low | 3 × ATR(14) below the low, plus a thesis stop |
| Target | 38–50% retrace of the drop, or the 50-day MA, whichever is closer | Full retrace to the 63-day high, or the price where the value percentile returns to 50 |
| Time stop | 20 trading days | 126 trading days |

- **Reward/risk gate:** a plan with (target − entry) / (entry − stop) < 1.5 is not shown. If both plans fail, the setup is dropped.
- **Thesis stop (position plan only):** a new 10-Q or 8-K makes the stock fail the value-trap gate.

### Exit alerts on logged positions

1. Stop hit (daily close below the stop).
2. Target hit.
3. Breakeven: at +1R, suggest moving the stop to entry.
4. Time stop: the horizon expired.
5. Thesis broken: the value-trap gate fails, or the 8-K cuts guidance.
6. Valuation normalized: the value percentile is ≥ 50.

Every setup is also **paper-tracked** automatically with the same rules. This is the live track record used in Section 4.

### Short plan

Entry after the bearish confirmation. Stop 2 × ATR above the recent high. Target the 50-day MA or the value percentile falling back to 50. Time stop 20 days. Extra exit on a guidance raise or a strong earnings beat. The card says "check borrow with broker", since borrow data isn't free.

### Out of scope

Position sizing, broker integration, intraday stops. All checks run on the daily close.

### Delivery (changed 2026-10-10)

- `analyzer scan` writes results to SQLite and to a JSON snapshot.
- A Claude Code project skill (e.g. `/daily-report`) runs the scan, reads the snapshot and writes the daily analysis as structured JSON. The dashboard displays it (see Section 5).
- Claude Code can read the 8-K text when writing the report. The engine's scoring still uses the deterministic keyword heuristic, so backtests stay reproducible.
- Positions are logged with `analyzer position add …` or by asking Claude.
- Exit alerts appear on the dashboard. Push notifications are deferred.
- The report can run unattended each morning with `claude -p` from cron.

### Revised build order (replaces the phase list in Section 1)

1. Data layer and universe.
2. Features, signals and research harness, ending in a go/no-go decision on the edge.
3. Claude report and dashboard, built together: scan CLI, JSON snapshot, position CLI, Claude Code report skill, static read-only dashboard, `analyzer serve`.
4. Optional, later: FastAPI backend for logging positions and triggering scans from the dashboard, plus Docker and a VPS.

The phase 2 gate still holds: no UI work until research confirms the edge.

---

## 6d. Design — Section 4: Research harness and flow (approved 2026-10-10)

### Point-in-time rules

- Fundamentals become known on their EDGAR `filed` date, not the period-end date.
- Market cap for valuation = price × shares outstanding from the latest filing as of that day.
- Returns use adjusted close, so dividends and splits are included.
- The universe on each date is the index membership on that date (rebuilt from the Wikipedia changes tables) plus the young listings that met the size and liquidity filters on that date.
- **Known gap:** yfinance often has no prices for delisted tickers. The harness reports price coverage of past members so the remaining survivorship bias is visible. The gap is worse for young listings, because failed IPOs disappear.

### Period and split

- Valuation percentiles need at least 3 years of history (5 preferred), so events start around 2014.
- **In-sample:** 2014–2021. Thresholds are tuned here only.
- **Out-of-sample:** 2022 to today, including the 2022 bear market and the recent loose-liquidity stretch.
- Every out-of-sample run is appended to a log, so repeated peeking is visible.

### Rule config

Thresholds and weights live in one versioned file, `rules.toml`. Research and the live scan both read it. Every stored setup records the rule version, so live and backtest results compare like for like.

### Metrics per event

- Forward returns at 5, 21, 63 and 126 trading days: raw, and in excess of SPY and of the industry/sector ETF.
- Win rate, median return, and max adverse excursion.
- Section 3 plan outcomes: target, stop or time stop.
- Breakdowns by drop type, sector/industry, flow bucket (hot / neutral / cold) and liquidity regime (loose / neutral / tight).
- **Young listings are a separate bucket** with their own statistics. They are not part of the main go/no-go.

### Do the gates add value?

Three groups are compared side by side:
1. SPY.
2. All dislocated stocks, with no gates.
3. Dislocated stocks that pass the undervalued and quality gates.

If group 3 does not beat group 2, the fundamentals layer is not earning its place.

### Overlap handling

A 42-day cooldown per ticker. Confidence intervals use bootstrap resampling by month blocks, because events cluster.

### Go/no-go (fixed in advance)

On the out-of-sample period, gated setups must show all of:
- n ≥ 100;
- median 63-day excess return vs SPY > 0;
- win rate ≥ 55%;
- better results than the ungated group.

Score quintiles must also rank forward returns roughly in order.

### Live tracking

Paper-tracked setups (Section 3) feed the same metrics. The research report shows backtest and live results side by side.

### Output

Results are written to CSV/JSON. A Claude Code skill (`/research-report`) writes the summary.

### Flow ("money going into a theme"; added 2026-10-10)

The owner's meaning of liquidity: money flowing into a sector or theme, such as semiconductors in 2026.

- **Industry flow:** the industry ETF's relative strength vs SPY over 63 and 126 days, and its dollar-volume trend (20-day average ÷ 120-day average).
- **Stock accumulation during the dip:** falling on light volume while up days have heavy volume, and on-balance-volume divergence.
- **Use:** a score component (weight 15), **not a gate**, so contrarian setups in unloved sectors stay eligible. Each setup shows a flow tag, for example `hot — semis, dollar volume 1.8× normal, RS +22% vs SPY`.
- **Risk:** hot industries are often expensive vs their own history. Peer-relative valuation helps, and research tests whether dips in hot industries really recover better.
- **Macro liquidity regime** (FRED, loose / neutral / tight) is context only: shown in the report and broken out in research, not scored. It becomes a score modifier only if research shows the edge differs by regime.
- **Liquid-stock floor:** 20-day average dollar volume ≥ $25M across the whole universe.

---

## 6e. Design — Section 5: Reports and dashboard (approved 2026-10-10, format amended the same day)

This replaces the original "API and dashboard" section. **The dashboard is where the report is read.** The engine produces the numbers, Claude Code writes the analysis, and a static dashboard shows both.

### Engine CLI (`analyzer`)

| Command | What it does |
|---|---|
| `update` | Incremental data fetch: prices, EDGAR, FRED, universe |
| `scan` | Computes features and setups. Writes to SQLite and to `reports/data/YYYY-MM-DD.json` |
| `explain TICKER` | Full JSON for any ticker: every gate's pass/fail, every metric and reason, whether flagged or not |
| `chart TICKER` | Static lightweight-charts HTML page, as in the spike |
| `position add/close/list` | Position log |
| `research run` | Backtest; writes CSV/JSON |
| `serve` | Serves the static dashboard and the report files on `localhost` |

### Snapshot JSON

- Run metadata: date, rule version, data freshness, liquidity regime, hot and cold industries.
- `setups[]`: score and its components, tier, reasons, valuation percentiles, quality metrics, drop type, flow tag, both plans, and flags (young / financial / REIT).
- `watchlist[]`: shock drops.
- `shorts[]`.
- `positions[]` with their alerts.
- Live track-record summary.
- A link to the latest 8-K for each setup.

### Claude Code skills (in the repo's `.claude/skills/`)

- **`/daily-report`:** runs `update` and `scan`, reads the JSON, reads the latest 8-K press release for the top ~10 setups and for anything with an alert, and writes `reports/YYYY-MM-DD.analysis.json`: a short market summary, plus per ticker the thesis, the drop explanation, risks and the 8-K takeaways.
- **`/ticker TICKER`:** on-demand deep dive using `explain`. It answers "why isn't X flagged?" as well as "why is it flagged?".
- **`/research-report`:** summarizes the research output.

### Dashboard screens

- **Today** (in order):
  1. Header: data freshness, liquidity regime, hot and cold industries, Claude's market summary.
  2. Position alerts (first, because they are time-sensitive).
  3. Ranked setup cards. Each card covers: why cheap, why quality, why it dropped (drop type plus Claude's reading of the 8-K), flow tag, plans, risks / what would break the thesis.
- **Ticker page:** an interactive lightweight-charts chart (candles, RSI, divergence lines, entry / stop / target lines) next to the valuation and quality numbers and Claude's analysis.
- **Tabs:** young gems, watchlist (shock drops, with what to check before they qualify), shorts, track record (live paper results).
- **History:** pick any past day's report.

### Format (amended 2026-10-10)

The engine writes the snapshot JSON; Claude writes the analysis JSON. The dashboard is a **static, read-only web app** (React + Vite + TypeScript + lightweight-charts) that reads both files. There is no backend in v1. Positions are logged with the CLI or by asking Claude, and the dashboard shows them. This replaces the earlier choice of a Markdown report with separate chart HTML files, which was clunky to browse every morning.

### Guardrail

The skill instructions require every number in the report to come from the JSON. Claude adds only qualitative reading of the 8-K text, with a link. No invented figures.

### Unattended runs

A systemd timer or cron job runs `claude -p "/daily-report"` after the US close, so the report is ready in the morning.

### Backend (later)

FastAPI is added only when editing from the dashboard is wanted (logging positions, triggering scans). It reads the same SQLite database and JSON schema, so no engine changes are needed. Not specified in detail now.

---

## 6f. Design — Section 6: Operations (approved 2026-10-10)

### Scheduling

- A systemd **user** timer runs at 07:00 Europe/Madrid, Tuesday to Saturday (01:00 ET), so after-hours 8-Ks and EDGAR's overnight processing are included.
- `Persistent=true` runs a missed job at the next boot if the laptop was off.
- One script, `bin/daily.sh`, runs `analyzer update`, then `analyzer scan`, then `claude -p "/daily-report"`.
- The script exits early on non-trading days (no new SPY bar).

### Data freshness

- Each provider records its last successful fetch and the latest date in its data.
- If prices are stale by more than 1 trading day, `scan` aborts with a clear error.
- Companyfacts lag is handled per ticker: the fallback reads the XBRL instance document attached to the latest 10-Q. If that also fails, the setup is tagged "fundamentals pending latest 10-Q".
- The dashboard header shows freshness for every source.

### Price fallback

`PriceProvider` tries a chain of sources. The first task of phase 1 is a small probe of Tiingo's free tier and Nasdaq's public endpoint against the full universe, to pick the fallback. Incremental runs need only one bar per symbol per day.

### Errors

- A failure for one ticker does not stop the scan. It is logged and listed on the dashboard.
- If more than 5% of tickers fail, `scan` fails.
- SEC requests stay under 10 per second and send the `SEC_USER_AGENT` header.
- Network errors are retried with backoff.

### Storage and config

- `data/` (gitignored): the SQLite database and the raw cache. Filings are immutable and cached forever. Prices and companyfacts refresh incrementally.
- `reports/` is gitignored by default.
- `.env`: `SEC_USER_AGENT` and `FRED_API_KEY` (free registration).
- `rules.toml` is committed to git.

### Tooling

Python 3.12 with uv, pandas, stdlib `sqlite3` with a numbered SQL migrations folder, Typer for the CLI, ruff and pytest. The dashboard uses React + Vite + TypeScript + lightweight-charts.

### Testing

- Unit tests for the pure functions in `features/`, with small fixtures.
- Recorded EDGAR fixtures for each known quirk: the NVDA capex tag change, the XOM CIK merge, COST's 16-week quarters, a bank, and the companyfacts lag.
- A no-look-ahead test: features computed as of date D ignore anything filed after D.
- A schema test for the snapshot and analysis JSON, so the dashboard can rely on them.
- A research sanity check: random entries show about 0 excess return vs SPY.
- No live network in default tests. `pytest -m live` runs opt-in smoke tests against the real APIs.

---

## 7. Next steps

1. Owner reviews the final spec.
2. Write the phase 1 implementation plan.

## 8. Open questions

- Which free fallback to use for daily prices, since Stooq is broken. Settled by the phase 1 probe (Tiingo free tier, Nasdaq public endpoint).
- The exact thresholds (drawdown %, gap vs sector, shock-day size). Set them from the research harness, not by hand.
- How much weight insider buying and 8-K guidance should carry in the quality score.
