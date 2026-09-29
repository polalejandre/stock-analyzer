# Stock Analyzer — Design Progress

- **Date:** 2026-09-29
- **Status:** Brainstorming in progress. The architecture is chosen (B). Section 1 of the design has been presented but not yet approved. Sections 2–6 are still to come. This is **not** the final spec yet. Once every section is approved, the final spec will live in `docs/specs/`.

---

## 1. Goal

A **personal** tool (not a product) that scans the US market every day and shows short-to-mid-term **buy / sell / short setups** on a web dashboard. Every setup comes with a clear explanation of why it was flagged.

The core idea, in the owner's words: what has made money lately is looking at **shorter windows**. The situations to catch are ones where a quality company's stock is going down (corrections, overreactions) while its fundamentals and macro say it should be going up. Buy low and expect a recovery, even a partial one. Example: Netflix has been doing badly in price lately, but the company's stats say otherwise.

### Success criteria

- Every morning the dashboard shows a **short, ranked list of setups**. Each setup shows why the stock is cheap, why the company is quality, the entry trigger (if any), and entry / stop / target / horizon.
- The research harness shows that the setup rules beat SPY over their holding horizon on point-in-time data.
- Positions logged in the dashboard get **exit alerts**.

---

## 2. Decisions made

| Topic | Decision |
|---|---|
| Audience | Personal tool. No accounts, billing or licensing concerns. |
| Universe | US **S&P 500 + S&P 400** (~900 stocks). |
| Horizon | **Both**: swing (1–4 weeks) and position (1–6 months). |
| Delivery | **Web dashboard**. |
| Hosting | **Local first**, packaged with Docker so it can move to a cheap VPS later. |
| Data budget | **Free only**. |
| Sell signals | **Exits** on logged positions and on tracked signals, **plus short ideas** (overvalued + deteriorating). |
| Event signals in v1 | **Yes**: 8-K earnings releases (Item 2.02) and Form 4 insider buying. |
| Architecture | **B**: Python engine + FastAPI + React / lightweight-charts. Build the engine first, the UI last. |
| Role of technicals | **Secondary.** RSI divergences (daily or weekly) confirm entry timing and raise confidence. A divergence is **not required**: a setup is actionable without one. |
| Role of valuation | **Core pillar.** Every buy setup must be undervalued, i.e. cheap vs the stock's own history and vs sector peers. |
| AI summaries (Claude API) | Optional, later. Not in v1. |

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

## 6. Design — Section 1: Architecture and components (presented, awaiting approval)

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

---

## 7. Remaining design sections (to do)

2. **Scoring model**: how value, quality, dislocation and technicals combine into one setup score. This includes the value-trap filter, the shock-vs-drift handling, and the bank-specific quality model.
3. **Setups and exits**: the entry / stop / target rules for the swing and position horizons, the exit rules for positions and signals, and the short rules.
4. **Research harness**: point-in-time rules, past index membership, out-of-sample split, metrics vs SPY, and live signal tracking.
5. **API and dashboard**: endpoints, screens (setups list, ticker detail, positions, research), charts.
6. **Operations**: scheduling, data-freshness checks, fallbacks, error handling, testing strategy.

## 8. Open questions

- Which free fallback to use for daily prices, since Stooq is broken. Candidates to evaluate: Tiingo free tier, Alpha Vantage free tier, and the SEC/Nasdaq public endpoints.
- The exact thresholds (drawdown %, gap vs sector, shock-day size). Set them from the research harness, not by hand.
- How much weight insider buying and 8-K guidance should carry in the quality score.
