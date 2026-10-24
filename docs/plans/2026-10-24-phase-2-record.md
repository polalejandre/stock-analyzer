# Phase 2 record: features, signals and the research harness (2026-10-24)

This is the complete record of phase 2. It covers:
- what was built;
- how it was built and reviewed;
- every decision taken on the owner's behalf;
- the first research results;
- what is still open.

The raw working files are copied verbatim into [`phase-2-sdd/`](phase-2-sdd/):
- the progress ledger;
- the per-task reports;
- the final review findings;
- the final fix report;
- the rules given to the implementers and reviewers.

## 1. Where things stand

- **Branch:** `phase-2-research`. It holds 52 commits on top of `main` at 063e12d (the phase-2 plan).
- **Tests:** 698 pass by default. 15 are deselected: 14 `live` network tests, plus 1 `realdb` test that runs against the real database.
- **Ruff:** clean.
- **Rules version:** `2026.10.24-4`.
- **Data:** the owner's database was backed up to `data/analyzer.db.bak-pre-phase2` before the live data refresh.
- **Research:** the first in-sample run is done, and its verdict is FAIL. In-sample is for tuning only.
- **Out-of-sample:** no run has been made. `docs/research/oos-runs.csv` contains only its header.
- **Next steps for the owner:**
  1. Fix the two open items in section 7.
  2. Review the in-sample results and tune thresholds on in-sample data only.
  3. Commit `rules.toml`.
  4. Run `uv run analyzer research run --period oos` once.

## 2. Documents

| Document | Role |
|---|---|
| `docs/specs/2026-10-10-stock-analyzer-design.md` | Base spec (sections 5–9 for this phase) |
| `docs/plans/2026-10-11-phase-2-kickoff.md` | Open decisions at the start of phase 2 |
| `docs/specs/2026-10-24-phase-2-design-addendum.md` | Phase-2 spec addendum, approved by the owner on 2026-10-24 |
| `docs/plans/2026-10-24-phase-2-features-signals-research.md` | The 27-task implementation plan |
| `docs/plans/2026-10-10-phase-1-status.md` | Status doc; its top section holds the phase-2 run numbers |
| `docs/plans/phase-2-sdd/ledger.md` | Full progress ledger: pre-flight scan, every ruling, every review outcome, every deferred item |
| `docs/plans/phase-2-sdd/task-reports/` | Each implementer's report (TDD evidence, deviations, fix rounds) |
| `docs/plans/phase-2-sdd/final-findings.md` | Final whole-branch review findings and the binding rulings for the fix wave |
| `docs/plans/phase-2-sdd/final-fix-report.md` | Report of the fix wave, including before/after numbers |

## 3. The owner's decisions (2026-10-24)

The owner settled the six open kickoff decisions, each on the recommended option. They then asked for the recommended option on every remaining design choice.

1. **Scope — core first.** Guidance tone, the watchlist, shorts, young listings and positions are left out of the first go/no-go run. Guidance tone is switched off in `rules.toml` (`guidance_tone_enabled = false`).
2. **Survivorship.** Renamed members are linked through their CIK (aliases). Members without prices are excluded, and their fate (acquired, other, unknown) is reported. No paid data.
3. **Missing quarters.** The latest 10-K annual value is used when it was filed at most 15 months earlier, tagged `ttm_source=annual`.
4. **Financials and REITs.**
   - Bank revenue = net interest income + noninterest income.
   - Insurers use their revenue tag.
   - Mortgage REITs, BDCs and IFRS filers are excluded and counted.
5. **Compute.** Features are computed on whole panels (dates × symbols). The live scan reuses the same code.
6. **Fallback tags.** Hindsight-chosen fallback tags are accepted. Affected events are flagged, and results are broken down by that flag.

The owner also approved the plan ("lets implement") and asked for subagent-driven execution ("ok go").

## 4. What was built

The layers depend in one direction only: `panel` ← `features` ← `signals` ← `research`.

- **Identity data (Tasks 2–4).**
  - Migration `003_identity.sql` adds the snapshot CIK, `ticker_aliases` and `member_fates`.
  - The Wikipedia CIK column and changes table are parsed.
  - `universe/aliases.py` holds the alias, fate and recycled-ticker logic.
  - Bank revenue and REIT property concepts are added.
  - On real data: 50 aliases, 31 recycled tickers excluded, and 480 members without prices excluded (145 acquired, 108 other, 227 unknown).
- **Panels (Tasks 5–8).**
  - Price panels (`panel/prices.py`), membership, liquidity and entities (`panel/universe.py`).
  - Point-in-time step series (`panel/steps.py`) and daily fundamental panels (`panel/fundamentals.py`). Every value enters on its own filing date and ages from it.
- **Features (Tasks 9–15).**
  - Value: ratios, own-history percentiles and peer medians.
  - Quality: YoY growth, margins, FCF, leverage, ROE and sub-scores.
  - Dislocation: gate 2.
  - Technical: RSI, ATR, pivots, divergence, reclaimed support.
  - Flow: industry tags and accumulation.
  - Insider cluster buying.
  - Liquidity regime.
- **Signals (Tasks 16–20).**
  - Variants; eligibility; gates 1, 3 and 4; plans and gate 5; score, tiers and reasons.
  - `signals/evaluate.py` is the single entry point that research and the future daily scan share.
- **Research (Tasks 21–26).**
  - Events with a cooldown per group, the period split and the purge.
  - Forward returns, MAE and plan simulation.
  - Statistics, month-block bootstrap and verdict.
  - Coverage report.
  - `analyzer research run`, with the out-of-sample guard and log.
  - An end-to-end CLI test.
- **Live run and docs (Task 27 and the fix wave).**

## 5. How it was built and reviewed

- **Process.** Subagent-driven development. Each task got a fresh implementer, then a task review covering spec compliance and code quality, then fix rounds with scoped re-reviews.
- **Final review.** A whole-branch final review ran on the most capable model, followed by one fix wave and one scoped re-review.
- **Fix rounds.** 16 of the 27 tasks needed at least one fix round. Task 7 (point-in-time steps) and Task 20 (evaluate fixture) needed two.
- **Look-ahead guard.**
  - Every feature has a truncation-invariance test: the value at date d must be the same with or without data after d. Tolerance is rtol 1e-9, with a self-test to prove the guard can fail.
  - An opt-in real-database test (`uv run pytest -m realdb tests/realdb`) checks `evaluate()` on 111 real symbols, cut in 2016, 2018 and 2020.
- **Out-of-sample protection.**
  - In-sample runs never read prices from `oos_start` onwards.
  - An out-of-sample run refuses to start if `rules.toml` or the addendum is uncommitted or untracked.
  - It writes a "started" log row before evaluating, and a final row after.

## 6. Research results

### First in-sample run (before the final fix wave)

Rules version 2026.10.24-3. Elapsed 8:16; peak memory 2.2 GiB.

| Group | n | Median 63-day excess vs SPY | Win rate |
|---|---|---|---|
| Setups (group 3) | 436 | −1.15% | 48.2% |
| Dislocated (group 2) | 3,787 | −0.10% | — |
| All dislocated members (secondary) | 4,726 | — | — |

### In-sample run after the fix wave (current)

Run `20261024-140642-in_sample`, rules version 2026.10.24-4. Elapsed 9:44; peak memory 2.6 GiB.

| Group | n | Median 63-day return | SPY same window | Median excess vs SPY (95% CI) | Excess vs reference ETF | Win rate (95% CI) | Median MAE |
|---|---|---|---|---|---|---|---|
| Setups (group 3) | 441 | +2.38% | +3.98% | −1.09% (−3.26%, +1.28%) | −0.60% | 48.5% (41.1%, 56.3%) | −10.2% |
| Dislocated (group 2) | 3,865 | +5.25% | +5.56% | −0.03% (−1.32%, +1.24%) | +0.40% | 49.9% (46.5%, 53.4%) | −9.7% |
| All dislocated members | 4,843 | +4.82% | +5.53% | −0.29% (−1.61%, +0.83%) | +0.32% | 49.2% (45.8%, 52.5%) | −9.9% |

Go/no-go tests on setups (in-sample, so tuning only):

| Test | Result |
|---|---|
| n ≥ 100 | PASS (441) |
| Median excess > 0 | FAIL |
| Win rate ≥ 55% | FAIL |
| Better than the dislocated group | FAIL |
| Top quintile above bottom quintile | PASS |

Score quintiles, median 63-day excess vs SPY: Q1 −1.84%, Q2 +0.76%, Q3 −1.31%, Q4 −2.04%, Q5 +2.41%.

Plan outcomes for setups:

| Plan | Time stop | Stop | Target | Thesis stop | No fill |
|---|---|---|---|---|---|
| Swing | 170 | 106 | 93 | — | 72 |
| Position | 52 | 169 | 96 | 52 | 72 |

Selected breakdowns (n, median 63-day excess vs SPY, win rate):

| Breakdown | Setups | Dislocated group |
|---|---|---|
| Shock drop | — (setups are drift only) | 2,191, +0.80%, 51.6% |
| Drift drop | 441, −1.09%, 48.5% | 1,674, −0.76%, 47.7% |
| Market correction | 159, +0.91%, 54.7% | 1,439, +1.44%, 53.8% |
| Idiosyncratic drop | 282, −1.81%, 45.0% | 2,426, −0.94%, 47.6% |
| Tier A | 26, +1.47%, 61.5% | 118, +0.56%, 52.5% |
| Industry hot | 17, +0.19%, 52.9% | 235, +1.47%, 52.8% |
| Industry cold | 41, −1.78%, 43.9% | 480, −1.10%, 47.3% |
| Financials | 38, +0.88%, 52.6% | 383, −0.55%, 47.8% |
| REITs | 7, +3.47%, 71.4% | 59, −1.11%, 47.5% |

### Reading of the result

The final reviewer checked the following and found no defects:
- gates, groups and benchmark windows;
- peer pools and price bases.

The FAIL appears to be a real result, not a bug. Two in-sample observations for tuning:
- Among dislocated events, the rows the gates reject did better than the rows they keep. Shock drops beat drift drops, and value-trap rejects beat gate-3 passes.
- Setups' raw win rate (not excess) is about 56%. Only the pre-registered excess-vs-SPY basis fails that test.

These are in-sample numbers. Any tuning must use in-sample data only, before the single out-of-sample run.

### Coverage (current run)

- **Index members with prices by year:** 2010 69%, 2011 71%, 2012 74%, 2013 66%, 2014 69%, 2015 68%, 2016 73%, 2017 75%, 2018 78%, 2019 81%, 2020 84%, 2021 85%. S&P 400 membership starts in 2013. The 2009 panel year has no members.
- **Variants:** standard 908, financials 147, REIT 57. Excluded: no us-gaap 23, no CIK 17, mortgage REIT 12.
- **Identity unverified** (no snapshot CIK, which covers all S&P 400 and S&P 500 before 2014-06): 555 symbols.
- **Skipped member ticker-days:** history 273,707; required inputs 200,271; variant 23,306; market cap 8,679.
- **Ticker-days on diluted shares:** 70,857. Annual-fallback TTM days: 42,213. Fallback-tag days: 100,642.
- **Reference-ETF fallback days:** 65,085 member days and 27,405 eligible days. These cover XLRE before 2015-10, XLC before 2018-06 and JETS before 2015-04.
- The liquidity regime is known on 100% of event days.

### Data refresh on 2026-10-24

- `update --step universe`: aliases 50, recycled 31, excluded 480, no failures.
- `update --step edgar`: re-extracted 1,799 companies after the tag-map change, with 1 of 1,780 failing. The step exited with code 1 because the SEC daily-index fetch returned HTTP 403, so `provider_status` was not updated and the daily-index scan is incomplete.
- Prices were not refreshed and end on 2026-10-09.

## 7. Open items

### Fix before tuning or the out-of-sample run

1. **Drifted YTD period ends** (`src/analyzer/data/edgar/quarters.py:96-110`).
   - A later year-to-date fact whose period end is 1–7 days off an existing link splits a pair that was already derivable. In full data, a quarter therefore appears later than it does in data cut at an earlier date.
   - This is conservative: values arrive late, never early. On real data it affects 16 CIKs, for example DE in 2016–17 and VTRS in 2021, and ITT, AVAV and STAA in the property check.
   - Fix: in point-in-time mode, merge links whose ends are within `SAME_QUARTER_DAYS`, keeping the first-filed one. Add a property test: `quarter_candidates(df[filed<=t]) == quarter_candidates(df)[filed<=t]` at every filing date.
2. **The in-sample guard does not cover the working rules file** (`src/analyzer/cli.py:186`).
   - The comparison with the committed `[research]` section runs only when `--rules` is passed.
   - An in-sample run with an edited `oos_start` in the working `rules.toml` could read out-of-sample prices without logging.
   - Fix: drop the `if rules_path is not None` condition, so the comparison always runs.

### Deferred (no material effect on results)

The full list, with locations, is in [`phase-2-sdd/ledger.md`](phase-2-sdd/ledger.md) (lines "minor (deferred)" and "parked"). Main items:

- **Recycled tickers.** Where snapshots carry no CIK, recycled tickers are only partly detected. About 35 real cases were found by name match (DV, WMS, BGC, PAY, AM, ACI, TW, HCC). Most do not overlap in time; WMS does.
- **Flow tag after a reference-ETF switch.** The tag is unknown for about 63 days after the switch.
- **Banks.** Bank revenue candidates are built from every direct and derived pair. A later derived quarter can replace an earlier direct one.
- **Leftovers and performance.**
  - `features.year_days` is no longer used.
  - Runtime is 9:44 against the 10-minute target (addendum 6.7), and peak memory is 2.6 GiB. An out-of-sample run over 2009–2026 will be about 37% larger.
- **Library use.** Calling `run_research(..., "oos")` directly as a library skips the CLI guard, by design.
- **Test coverage gaps.** These are listed per task in the ledger.

## 8. Decisions taken on the owner's behalf

The owner asked for the recommended option throughout. Every ruling below is recorded verbatim in the ledger, together with its reasoning. "Cost if wrong" says what to rework if the ruling turns out wrong.

| # | Decision | Cost if wrong |
|---|---|---|
| R1 | Update existing tests for the new `cik` column in parsed Wikipedia tables | Test churn only |
| R2 | Update existing tests for the extra alias step in the universe update | Test churn only |
| R3 | Update existing tests pinned to the tag-map fingerprint | Test churn only |
| R4 | Work on a branch in the main checkout, not a separate worktree, so the live run can use the real `data/` and `.env` | Main checkout sits on the branch until it is merged |
| R5 | Plan code is a starting point. Implementers fix plan bugs that break the plan's own tests, and report each fix | Small deviations, all listed in the task reports |
| R6 | A ticker whose snapshots name more than one company (after mapping CIK mergers) is excluded as `recycled` | The current owner's own history is lost for those tickers (31 on real data) |
| R7 | The alias row stores the CIK that matched | None |
| R8 | The truncation guard uses rtol 1e-9, needs non-empty cut dates, and has its own self-test | A feature test may need a documented tolerance |
| R9 | Debt emits a row at each component's first filing | More steps, none wrong |
| R10 | The fallback flag follows the first-filed version of each period | A later fallback restatement goes unflagged (rare) |
| R11 | Quarters and annual values use first versions; 10-Q/A amendments are ignored | An amended quarter keeps its original value |
| R12 | Annual values are preferred only when more than `SAME_QUARTER_DAYS` newer. Latest-value steps prefer the shortest duration. Every step builder has a truncation test | None |
| R13 | Accept quarter-key merging within `SAME_QUARTER_DAYS` instead of snapping period ends | Harmless shifts of a few days |
| R14 | Quarter, instant and diluted-share fields age on their own filing date. Diluted shares are split-adjusted from their filing date | None |
| R15 | `has_property` and `has_us_gaap` stay static per symbol | A REIT that began tagging property late is scored as a REIT in earlier years |
| R16 | Peers for a metric are the symbols whose variant uses that metric | Bank P/E peers exclude standard stocks in the same SIC group |
| R17 | New `revenue_decline_known` flag, so an unknown quarter counts as a skipped rule | None |
| R18 | FCF sub-score is NaN when last year's FCF is unknown | Young FCF histories get no FCF sub-score in their first year |
| R19 | Reclaimed support is anchored on the break bar | Signal fires on different days (score only) |
| R20 | Pivot ties, flat-price RSI = 50, RSI/ATR NaN after the last price, MA added to the truncation test | None |
| R21 | Hot means exactly the top 20% and cold the bottom 20%. With fewer than 5 ranked industries the tag is unknown (`flow_min_industries`) | Fewer hot/cold tags |
| R22 | Up/down days use adjusted closes; an unknown up/down ratio scores neutral | None |
| R23 | Regime thresholds live in `rules.toml [regime]`; publication lags stay code constants | One more rules section |
| R24 | "The universe" is index membership plus liquidity. Peers and industry flow rank within it | Peer medians include young or incomplete filers |
| R25 | The evaluate fixture gets negative cases for every gate, dividends and an insider purchase | Slower fixture |
| R26 | The "only in dislocated_all" fixture stock is an index member with an excluded variant | None |
| R27 | Gates 3 and 5 are made load-bearing in the integration tests (mutation-checked) | None |
| R28 | Delisted stocks exit at their last valid close. Time stop beats thesis on the same day. Panel dates are asserted | Takeovers count at their last traded price |
| R29 | Quintile ties are broken by a stable hash of (symbol, date) | None |
| R30 | In-sample runs load prices only up to the day before `oos_start` | None |
| R31 | Coverage, guidance and win-basis lines are printed; the OOS log is written before results print; `git_state` is hardened; `dirty_tree` column added | One more log column |
| R32 | Back up the database before the live update | 1.7 GB of disk until deleted |
| R33 | Point-in-time quarter candidates: the earliest-known value first, later ones replacing from their own filing date. Opt-in real-database truncation test | None |
| R34 | Year-earlier values come from the steps (`*_ttm_ly`, `equity_avg_ly`), not a 252-day shift | About 200 MB more panel memory |
| R35 | Reference ETF falls back day by day: industry, then sector, then SPY. Fallback days are counted | Early XLC/XLRE/JETS names are compared with a broader index |
| R36 | Regime is classified from at least 5 known indicators, with proportional thresholds (the same as 4 and 2 when all 6 are known) and a bounded forward fill | History's regime uses 5 indicators (the HY spread series starts in 2023) |
| R37 | Refuse `--rules` files that change `[research]`. OOS writes a "started" log row first | Two log rows per OOS run |
| R38 | Breakdown renamed to `flow` and `ref_etf` added. Revenue rule is known when TTM growth ≥ 0. In-sample re-run and status update | None |

## 9. Resuming

- **Code:** branch `phase-2-research`. `git log main..phase-2-research` lists all 52 commits.
- **Records:** this file and [`phase-2-sdd/`](phase-2-sdd/). A gitignored working copy of the same workspace remains under `.superpowers/sdd/2026-10-24-phase-2-features-signals-research/`.
- **Local-only files** (gitignored, not in this repository):
  - `data/analyzer.db` and its backup;
  - `data/cache/`;
  - `.env`;
  - research outputs under `data/research/`.
  To continue on another machine (for example Windows, preferably through WSL), copy `data/`, `.env` and `.superpowers/` across, then run `uv sync` and `uv run pytest -q`. A few helper steps assume Linux (`/usr/bin/time -v`, bash scripts).
- **Commands:**
  - `uv run analyzer research run`: in-sample by default; `--rules PATH` runs a tuning file.
  - `uv run analyzer research run --period oos`: out-of-sample, once, after the rules are frozen and committed.
  - `uv run pytest -m realdb tests/realdb`: real-database truncation check.
