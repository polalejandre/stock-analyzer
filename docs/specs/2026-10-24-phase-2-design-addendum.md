# Phase 2 design addendum: features, signals and the research harness

Date: 2026-10-24. Base spec: `docs/specs/2026-10-10-stock-analyzer-design.md` (sections 5–9).
Inputs: `docs/plans/2026-10-11-phase-2-kickoff.md` and `docs/plans/2026-10-10-phase-1-status.md`.

This addendum covers only what the base spec leaves open, or what phase 1 changed. Where it says
nothing, the base spec applies. The owner settled the six open decisions on 2026-10-24 and asked
for the recommended option on every remaining design choice ("do all the recommended for now").

Phase 2 ends with the first go/no-go run of spec section 9.6.

---

## 1. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Scope before the first go/no-go | **Core first** (section 2). Guidance tone is left out of the first run. |
| 2 | Survivorship | **Alias table plus explicit exclusion.** Renames are recovered through the CIK. Delisted names without prices are excluded and reported by fate. No paid source. |
| 3 | TTM when quarters are missing | **Annual fallback.** Use the latest 10-K annual value when it was filed at most 15 months before `as_of`, tagged `ttm_source=annual`. Otherwise the ticker is skipped on that date. |
| 4 | Financials and REITs | **Map bank revenue** = net interest income + noninterest income. Insurers use their revenue tag. Mortgage REITs, BDCs and IFRS filers are excluded and counted. |
| 5 | Compute strategy | **Panel-native features** (tickers × dates). The live scan calls the same functions and keeps the last date. Truncation-invariance tests guard against lookahead. |
| 6 | Fallback-tag hindsight | **Accept and flag.** Events whose inputs use fallback tags carry `fallback_tags=true`, and the go/no-go metrics are broken down by that flag. |

---

## 2. Scope of the first go/no-go run

### 2.1 In scope

- Variants: standard, financials and REITs (spec 7.4).
- Features: value, quality, dislocation, flow, technical, insider cluster buying (spec 5.1–5.5).
- Liquidity regime (spec 5.6), as a research breakdown only (section 7.3).
- Signals: buy gates 1–5, score, tiers, swing and position plans, reason strings (spec 7.1, 7.3,
  7.6, 8.1).
- Research harness: event study, groups 1–3, plan outcomes, bootstrap intervals, go/no-go
  verdict, coverage report (spec 9).

### 2.2 Out of scope, in this order afterwards

1. Guidance tone (8-K Item 2.02 EX-99.1 download and keyword parser).
2. Watchlist and the shock-drop promotion (spec 7.2).
3. Shorts (spec 7.5).
4. Young listings and the young variant (spec 2, 7.4).
5. Positions, exit alerts and paper tracking (spec 8.2–8.4). The harness simulates plan exits
   itself (section 6.4), but nothing is stored as a position.

### 2.3 What the omission of guidance tone changes

This is recorded before any result is seen, so the go/no-go stays fixed in advance:

- Gate 3 runs without the rule "latest 8-K guidance tone is lowers / cuts".
- The quality score averages four sub-scores (growth, margin trend, FCF, balance sheet) in place
  of five.
- `rules.toml` carries `guidance_tone_enabled = false`. The first-run report prints it.

### 2.4 Who is not scored

- **Not enough own history:** the base spec routes stocks with less than 3 years of own
  fundamentals history to the young variant. That variant is out of scope, so these stocks are
  skipped on those dates and counted in the coverage report.
- **Gate 4 still applies.** Shock drops are rejected. They are not tracked anywhere until the
  watchlist exists.

---

## 3. Data-layer changes

Phase 2 needs these changes to the phase-1 data layer. Each one has a test.

### 3.1 Ticker aliases (decision 2)

- New migration `003_ticker_aliases.sql`: table `ticker_aliases (ticker, price_symbol, cik,
  valid_from, valid_to, source)`.
- Automatic entries: a past index member without prices whose CIK maps today to another ticker
  that has prices (for example ANTM→ELV, ABC→COR, FB→META).
- Manual entries: `src/analyzer/data/aliases.toml`, for renames the CIK route misses. Source
  `manual`.
- Reads: the price loader resolves a ticker to its price symbol per date range. Universe
  membership keeps the historical ticker.

### 3.2 Fate of delisted members (decision 2)

- Each past member that still has no prices after aliasing gets a fate: `acquired`, `other` or
  `unknown`.
- The fate comes from keywords in the Wikipedia changes table's reason text ("acquired",
  "merger", "bought by" give `acquired`). No keyword gives `other`; no reason text gives
  `unknown`.
- Bias reading: excluded acquired names tend to hide takeover premiums (gains). Excluded failed
  names hide losses. Both groups 2 and 3 lose the same names, so the group-3-versus-group-2 test
  is less exposed than the test against SPY.

### 3.3 Fundamentals fixes

- **Bank revenue (decision 4):** new concepts `net_interest_income` and `noninterest_income`.
  For banks, revenue = their sum when the filing has no revenue tag. `EXTRACTION_RULES` is bumped,
  so stored facts are re-extracted.
- **Debt double count:** `long_term_debt` can read `LongTermDebt`, which already includes the
  current portion, while `short_term_debt` reads `LongTermDebtCurrent`. Total debt must count the
  current portion once.
- **Negative derived quarters:** a derived quarter below zero for a concept that cannot be
  negative (revenue, capex) is treated as missing. The annual fallback then applies (the BAX
  Q3 2015 case).
- **Stale share counts:** a cover-page share count filed more than 15 months before `as_of` is
  not used; diluted weighted-average shares are used instead (spec 3.3 already names that
  fallback). The audit reports how often this happens.

### 3.4 Deferred data items

- Per-class share counts (BRK). BRK-B has no market cap, so it is excluded and counted.
- FRED first-release values (ALFRED). Section 7.3 explains why the regime can wait.
- Read-side provider interfaces (M2). The panel loader reads SQLite directly through `store/`.
- Young-listing survivorship (I2). Young listings are out of scope.
- Capex reported only under company extension tags (COP, PSX, NEE and about 30 more). FCF is
  missing for those periods and is handled as in section 5.4.

---

## 4. Architecture (decision 5)

### 4.1 Modules

```
src/analyzer/
  panel/        # loads point-in-time panels from SQLite: prices, membership, fundamentals steps
  features/     # pure functions over panels: value, quality, dislocation, flow, technical,
                # insider, regime
  signals/      # gates, score, tiers, plans, reasons; variant selection
  research/     # events, outcomes, plan simulation, statistics, coverage, report, CLI command
```

Each module has one direction of dependency: `panel` ← `features` ← `signals` ← `research`.
`features` and `signals` do not read the database.

### 4.2 Panels

- **Price panels:** wide DataFrames indexed by trading date, one column per ticker. They hold
  open, high, low, close, adjusted close and volume, after alias resolution. Reference ETFs and
  SPY are columns in the same panels.
- **Membership panel:** a boolean frame. True when the ticker is an index member on that date and
  its 20-day average dollar volume is at least $25M.
- **Fundamentals steps:** for each company and concept, one row per `filed` date with the TTM
  value as known on that date, plus the TTM value one year earlier. When several filings report
  the same period, the latest one filed on or before the date wins. The steps are as-of joined
  onto the trading-date index, so each value appears from its `filed` date onwards (spec 3.3).
- **Price-dependent ratios** (P/E, P/B, P/S, P/FFO, EV/EBITDA, FCF yield) are vectorized from
  the joined steps and the close.

### 4.3 Wide panels, then long rows

- Price-only features (dislocation, flow, technical) are computed on the whole wide panel.
- Gate 2 (dislocation) is price-only. It turns the panel into long rows: one row per dislocated
  ticker-day inside the universe.
- Fundamental features, the other gates, the score and the plans are evaluated on those rows only.
- Reason strings are built only for rows that become events (research) or setups (live).

### 4.4 Live scan and research share the code

The live scan (phase 3) builds the same panels up to today and keeps the last date. There is no
separate per-date code path.

### 4.5 Lookahead guard

- **Truncation invariance:** for every feature function, the value at date d computed from data
  cut at d must equal the value at d computed from the full panel. Tests check this on fixture
  data, and on a sample of real tickers and dates.
- **Delayed confirmation:** anything that needs bars after a point (swing pivots, weekly bars) is
  known only once those bars exist. A pivot at day p with k confirmation bars is usable from day
  p + k. A weekly bar is usable after the week's last trading day.

---

## 5. Feature details the base spec leaves open

Every number below is a starting value in `rules.toml`, tuned on the in-sample period only.

### 5.1 TTM assembly (decision 3)

1. Four consecutive quarters, real or derived, give the TTM value (`ttm_source=quarters`).
2. Otherwise the latest 10-K annual value filed at most 15 months before `as_of` is used
   (`ttm_source=annual`).
3. Otherwise the concept has no TTM value on that date.

YoY comparisons use the same rule on both sides. Events carry `ttm_source=annual` when any
input used the annual fallback, and the harness breaks the results down by that tag.

### 5.2 Fallback tags (decision 6)

An event carries `fallback_tags=true` when any of its fundamental inputs came from a summed
fallback row (`Concept.fallback_tags`). The harness reports the go/no-go metrics with and without
those events. The flag is a disclosure, not a filter.

### 5.3 Variants

| Variant | Applies to | Value metrics | Quality and trap changes |
|---|---|---|---|
| Standard | everything else | P/E, EV/EBITDA, FCF yield, P/S | spec 5.2 and gate 3, without guidance tone |
| Financials | SIC 6000–6411 (banks, card issuers, brokers, asset managers, insurers) | P/E, P/B | Growth: revenue and EPS. Margin rule replaced by "TTM ROE down > 5 points YoY". No EV/EBITDA, FCF or net-debt rules. Balance-sheet sub-score dropped. |
| REITs | SIC 6798 with a `RealEstateInvestmentPropertyNet` or `RealEstateInvestmentPropertyAtCost` fact | P/FFO, EV/EBITDA, P/S | Spec 5.2 without the FCF rule and FCF sub-score. Net debt / EBITDA cap starts at 7.0, because REITs carry more debt by design. |
| Excluded | SIC 6798 without either property fact (mortgage REITs), SIC 6726 (BDCs and funds), filers with no `us-gaap` facts (IFRS) | — | Counted in the coverage report. |

- FFO = TTM net income + TTM depreciation and amortization.
- ROE = TTM net income / average stockholders' equity over the last five quarter ends.
- REITs keep three value metrics, so the "cheaper than peers on ≥ 2 metrics" test stays
  meaningful. P/FFO replaces P/E.

### 5.4 Missing inputs

- A value metric with a missing or non-positive denominator is dropped (spec 5.1).
- Gate 1 needs at least two value metrics.
- A gate-3 rule whose inputs are missing is not evaluated. The event lists it in its reasons
  ("FCF rule skipped: no capex data") and counts it in `missing_inputs`. Revenue and either
  operating income (standard, REIT) or net income (financials) are required; without them the
  ticker is skipped on that date.
- The harness breaks results down by `missing_inputs > 0`.

### 5.5 Technical definitions

- **Swing low / high:** a bar whose low (high) is the lowest (highest) of the k bars on each
  side, with k = 5 to start. Usable from p + k (section 4.5).
- **Bullish RSI divergence:** the last two swing lows inside a 40-bar lookback. Price makes a
  lower low, RSI(14) makes a higher low. Bearish is the mirror image. On weekly bars: same rule,
  k = 2, lookback 26 weeks.
- **Reclaimed support:** after the close broke below the prior swing low, it closes back above
  that level within 10 bars.
- **Plan swing low:** the lowest low since the 63-day high (the drawdown low).

### 5.6 Insider cluster buying

- Form 4 rows with code `P`, known from their `filing_date`, inside the drawdown window (from
  the 63-day high date to `as_of`).
- CEO and CFO are matched on the officer title text.
- Dollar value = shares × price.

### 5.7 Flow

- The industries ranked for the flow tag are the set of reference ETFs used by the universe on
  that date.
- Stock accumulation is computed over the drawdown window, like insider buying.

---

## 6. Research harness details

### 6.1 Eligible pool and groups

- **Eligible pool:** universe members (membership panel true) that are scored on that date:
  enough history, a supported variant, required inputs present.
- **Group 1:** SPY.
- **Group 2:** eligible-pool rows that pass gate 2. This keeps groups 2 and 3 on the same pool,
  so the comparison measures gates 1, 3, 4 and 5 only. A secondary line reports all dislocated
  universe members, scored or not.
- **Group 3:** eligible-pool rows that pass gates 1–5.

### 6.2 Events and cooldown

- An event is the first qualifying day of a ticker in a group. The 42-trading-day cooldown
  (spec 9.5) runs per ticker **per group**. A setup can first qualify days after the first
  dislocated day, for example once a new 10-Q lands, so group-3 events are not a subset of
  group-2 event days.
- The signal is known at the close of day t. Entry is the open of day t + 1. Forward returns,
  excess returns and plan outcomes are measured from that open.

### 6.3 Split and purge

- In-sample: events from 2014-01-01. Thresholds are tuned here only.
- An in-sample event counts only when its longest forward window (126 trading days) ends before
  2022-01-01, so no in-sample result uses out-of-sample prices.
- Out-of-sample: events from 2022-01-01 to the latest date with a full 63-day window. Shorter
  windows are reported but not used for the go/no-go.

### 6.4 Plan simulation

- Entry: the next open, only when it is inside the entry zone. Otherwise the outcome is
  `no_fill`.
- Exits on the daily close: stop, target, time stop. For the position plan, also the thesis
  stop: a new 10-Q or 8-K after which gate 3 fails exits at the next open.
- The valuation target (median percentile back to 50) is the median, across metrics, of the
  price at which each metric reaches its own 50th percentile.

### 6.5 Statistics and verdict

- Confidence intervals: bootstrap with 2,000 resamples in calendar-month blocks.
- The run prints the four go/no-go tests and the quintile test (median 63-day excess return of
  the top score quintile above the bottom one), each pass or fail, and the overall verdict.
- Breakdowns: spec 9.4, plus `ttm_source`, `fallback_tags`, `missing_inputs > 0`, variant and
  liquidity regime.

### 6.6 Coverage report

Printed and saved with every run:

- universe size per year, and the share with prices;
- aliased tickers;
- excluded members by fate (`acquired`, `other`, `unknown`);
- skipped ticker-days by reason: too little history, excluded variant, missing required inputs,
  no market cap.

### 6.7 Running it

- `analyzer research run --period in-sample` is the default. `--rules PATH` runs with another
  rules file, for tuning. There is no automatic optimizer: few, deliberate threshold changes
  overfit less.
- `analyzer research run --period oos` refuses to run when `rules.toml` or this addendum has
  uncommitted changes. Each run appends date, rules version, git commit and headline metrics to
  `docs/research/oos-runs.csv`.
- Outputs: CSV and JSON under `data/research/<run-id>/` (spec 9.7).
- Target: a full in-sample run in under 10 minutes on the owner's machine. Panels are kept in
  memory; a disk cache is added only if that target is missed.

---

## 7. Other choices

### 7.1 `rules.toml`

New sections: `[features]`, `[gates]`, `[score]`, `[variants.financials]`, `[variants.reit]`,
`[plans]`, `[research]` (split dates, purge, cooldown, horizons, bootstrap, go/no-go thresholds).
`guidance_tone_enabled = false` sits in `[gates]`. The version is bumped for the first run.

### 7.2 Score

Spec 7.3 applies, with these changes:

- the quality component averages only the sub-scores that exist for the variant;
- the dislocation-type value "promoted shock" is unused until the watchlist exists.

### 7.3 Liquidity regime

- The regime is a research breakdown only (spec 5.6); it does not touch gates or score, so it
  cannot change the go/no-go.
- FRED values are shifted by a conservative publication lag per series (weekly series one week,
  monthly series five weeks). First-release values (ALFRED) are deferred.

---

## 8. Testing

- Unit tests per feature and gate on small fixture panels with known answers.
- Truncation-invariance tests for every feature function (section 4.5).
- Point-in-time tests: a fact appears on its `filed` date, never before; a restatement replaces
  a value only from its own `filed` date.
- Variant tests: a bank, an insurer, an equity REIT and an excluded mortgage REIT from fixtures.
- Harness tests: cooldown per group, the purge, `no_fill`, thesis stop, bootstrap on a fixed
  seed.
- One end-to-end test runs the harness on a small fixture database and checks the output files
  and the verdict.
- No test calls the network (the phase-1 network guard stays on).

---

## 9. Done when

1. The data-layer changes of section 3 are merged and the audit shows the new coverage.
2. An in-sample run produces the outputs of section 6 within the time target.
3. The owner has reviewed the in-sample results and frozen the rules version.
4. One out-of-sample run is logged in `docs/research/oos-runs.csv` with its verdict.
