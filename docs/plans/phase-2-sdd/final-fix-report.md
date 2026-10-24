# Final review fix wave (R33-R38): report

Branch `phase-2-research`, base `d1c2e75`. Eight commits, each with the trailer
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and staged by explicit path. Nothing pushed
or merged. `analyzer update` and `--period oos` were never run. `docs/research/oos-runs.csv` is
unchanged (`git diff --quiet` clean, and the OOS tests assert its bytes are unchanged). Nothing under
`data/` or `.superpowers/` was deleted.

| Finding | Commit | Subject |
|---|---|---|
| I1 / R33 | 84a4047 | fix: point-in-time quarter candidates so fundamentals are truncation-invariant on real data |
| I2 / R34 | 79cc421 | fix: YoY features compare with the same figure a year earlier, not a 252-day shift |
| I3 / R35 | 9c5f2cc | fix: reference ETF falls back per day (industry, sector, SPY) until it has a 63-day return |
| I4 / R36 | c11d7e5 | fix: classify the liquidity regime from the indicators known that day |
| I5 / R37 | 5e2c98f | fix: close the OOS guard and log bypasses |
| M1 / R38 | bf14a59 | fix: name the flow breakdown "flow" and add a reference-ETF breakdown |
| M4 / R38 | ba96f20 | fix: revenue trap rule is known when TTM revenue growth is not negative |
| M5 / R38 | 8572960 | docs: phase-2 in-sample run after the final-review fix wave |

Final state: `uv run pytest -q` gives `698 passed, 15 deselected in 37.76s` (672 before the wave,
no warnings), and `uv run ruff check src tests` gives `All checks passed!`.

---

## I1 / R33: fundamentals not truncation-invariant (84a4047)

### Change

- `data/edgar/quarters.py`: the candidate builder is factored out (`_candidates`).
  `discrete_quarters` is unchanged: one quarter per period, direct beats derived. The audit and
  `load_quarters` keep using it. The new `quarter_candidates(df)` returns every candidate, direct
  and derived, each with its own filed date, sorted in a total order. Point in time, a derived
  candidate is also built where a direct quarter exists. A YTD chain link is the first-filed fact
  of its period end; `discrete_quarters` still keeps the last.
- `panel/steps.py` `quarter_steps`: applies the candidates in filing order. The earliest-known
  value is used first, and a later candidate replaces it from its own filed date.
  `SAME_QUARTER_DAYS` merging is unchanged. On one filing date a direct quarter is applied after a
  derived one, so it wins. Remaining ties are broken by end, then start, then value, so the order
  of the input never matters.
- `panel/fundamentals.py`: `company_steps` uses `quarter_candidates`, also for the bank NII and
  noninterest income parts.
- `data/edgar/facts_read.py` (`load_concept(all_versions=True)`): a predecessor CIK's version is
  now dropped only when the current CIK had already reported that period on time, on or before
  the predecessor's filing date. Before, it was dropped whenever the current CIK reported the
  period at any time, including later.
- New opt-in test `tests/realdb/test_truncation.py`, with the `realdb` marker registered in
  `pyproject.toml`. Default runs use `addopts = "-m 'not live and not realdb'"`. The test skips
  when `data/analyzer.db` is missing.
  - It opens the database with `?mode=ro` and builds in-memory copies of the needed rows.
  - The sample is 111 symbols: 100 random (seed 3) plus 15 named (AAPL, JPM, BAC, O, SPG, WFC, AMT,
    BAX, COP, MET, PLD, KO, GE, HAIN, WDC), plus every reference ETF.
  - The full run uses data to 2021-12-31. The cuts are 2016-08-15, 2018-06-29 and 2020-03-31.
  - It compares every fundamental panel cell of every sampled symbol, the evaluate rows, the
    eligible/trap/member cells, the dislocated_all rows and the regime days.

### Two causes outside the ruling's wording, found by the real-database test

The ruling covered the direct/derived ranking (HAIN). After that fix, the real-database test
still failed at the 2018 cut, for three more reasons. Each one breaks the same binding rule, so I
fixed them in the same commit:

1. **AVGO (merged CIKs).** The predecessor's Q1 FY2018 (filed 2018-03-15) was hidden in full data,
   because the successor reported the period on time later (2018-12-21). Fixed by the point-in-time
   predecessor filter described above.
2. **RBC (drifted-start link).** A comparative FY fact with a start one day off (2017-04-01 instead
   of 2017-04-02), filed 2020-05-20, became the chain link for 2018-03-31. That moved the derived
   Q4 from 2018-05-30 to 2020. Fixed: point in time, a chain link is the first-filed fact of its
   period end.
3. **SUN (tie order).** One 10-K tags two Q4 versions (a 92-day quarter and a 97-day stub) with the
   same filed date. Which one won depended on the order of the input rows. Fixed: a total sort
   order; the latest start, so the shortest period, wins.

### Tests

New tests:
- `test_quarter_candidates_keep_direct_and_derived_with_their_own_filing`
- `test_late_direct_quarter_replaces_the_derived_one_only_from_its_filing`
- `test_late_direct_quarter_is_truncation_invariant`
- `test_same_day_direct_quarter_wins_over_derived`
- `test_drifted_start_refiling_of_a_link_does_not_move_the_derived_quarter`
- `test_same_day_candidates_for_one_quarter_do_not_depend_on_input_order`
- `test_quarter_reported_directly_years_later_keeps_its_early_derived_value`, a database-level HAIN
  case with a truncation check, in `tests/panel/test_fundamentals.py`

Updated test: `test_all_versions_drops_predecessor_periods_the_current_cik_reports`. It used to
assert that full data drops the predecessor's 7777, filed 2022-05-20, which is before the current
CIK's 2022-05-27 report. That expectation was itself a lookahead: its own `as_of` check showed 7777
as known. It now expects `[500, 7777, 1000, 1050]`. A new predecessor row filed after the current
CIK's report (8888) is still dropped.

RED, database-level test against the old code:
```
>       assert rev.loc["2016-01-04"] == 46 and not full["annual"]["AAA"].loc["2016-01-04"]
E       assert (np.float32(46.0) == 46 and not np.True_)
1 failed, 14 passed in 1.44s
```
(The step-level tests failed to import `quarter_candidates` on the old code.)

GREEN:
```
tests/panel/test_steps.py: 37 passed in 0.64s
tests/panel tests/data: 272 passed in 4.67s
uv run pytest -q: 679 passed, 15 deselected in 36.73s
uv run ruff check src tests: All checks passed!
```

### Real-database truncation test

Baseline, the HEAD `d1c2e75` code run against the new test, log `data/realdb-trunc-before-fix.log`
(lines cut off at the end):
```
cut 2016-08-15: fundamental cells differing {'revenue_ttm': 505, 'operating_income_ttm': 201, 'net_income_ttm': 409, 'eps_ttm': 441, 'da_ttm': 455, 'capex_ttm': 369, 'revenue_q0': 239, 'revenue_q0_ly': 239, 'revenue_q1': 483, 'revenue_q1_ly': 483, 'annual': 567}; rows 1903 vs 1903, ... column diffs {'missing': 62, 'annual': 108, 'growth_score': 108, 'margin_score': 70, ... 'setup': 9, ...
cut 2018-06-29: fundamental cells differing {'revenue_ttm': 391, ... 'annual': 606}; ... cell diffs {'eligible': 0, 'trap_fails': 66, 'member': 0}, ...
cut 2020-03-31: fundamental cells differing {'revenue_ttm': 75, ... 'annual': 103}; ...
1 failed in 130.84s
```

After I1, with only the direct/derived change (`data/realdb-trunc-i1.log`), AVGO, RBC and SUN still
differed at the 2018 cut:
```
cut 2018-06-29: ... column diffs {'nd_ebitda': 2, 'pct_ev_ebitda': 2, 'pct_ps': 2, 'median_pct': 2, 'val_target': 2, 'c_value': 2, 'c_quality': 2, 'score': 2}
```
That run also showed eligible/trap/member cell differences, but those came from a defect in the
test. It compared symbols with no prices before the cut as NaN. The test now reindexes with
`fill_value=False`.

After the full I1 commit (`data/realdb-trunc-i1c.log`):
```
cut 2016-08-15: fundamental cells differing 0; rows 1903 vs 1903, only-full 0, only-cut 0, column diffs {}, cell diffs {'eligible': 0, 'trap_fails': 0, 'member': 0}, dislocated_all diffs 0, regime diffs 0
cut 2018-06-29: fundamental cells differing 0; rows 3027 vs 3027, ... column diffs {}, cell diffs {... 0 ...}, dislocated_all diffs 0, regime diffs 0
cut 2020-03-31: fundamental cells differing 0; rows 5211 vs 5211, ... column diffs {}, ... 0
1 passed in 135.92s (0:02:15)
```

Final run, with all code (`data/realdb-trunc-final.log`):
```
cut 2016-08-15: fundamental cells differing 0; rows 1940 vs 1940, only-full 0, only-cut 0, column diffs {}, cell diffs {'eligible': 0, 'trap_fails': 0, 'member': 0}, dislocated_all diffs 0, regime diffs 0
cut 2018-06-29: fundamental cells differing 0; rows 3203 vs 3203, only-full 0, only-cut 0, column diffs {}, cell diffs {'eligible': 0, 'trap_fails': 0, 'member': 0}, dislocated_all diffs 0, regime diffs 0
cut 2020-03-31: fundamental cells differing 0; rows 5387 vs 5387, only-full 0, only-cut 0, column diffs {}, cell diffs {'eligible': 0, 'trap_fails': 0, 'member': 0}, dislocated_all diffs 0, regime diffs 0
1 passed in 149.08s (0:02:29)
```
The I2 and I3 intermediate runs also passed (`data/realdb-trunc-i2.log`, `data/realdb-trunc-i3.log`).

---

## I2 / R34: YoY paired the wrong periods (79cc421)

### Change

- `panel/steps.py` emits year-ago values:
  - Quarter path: `ttm_ly` is the sum of the four consecutive quarters ending about 365 days
    before q0. It uses the existing `_year_ago` tolerance, through a new `_year_ago_end`.
  - Annual path: `ttm_ly` is the prior fiscal year (`_year_ago` on the annual ends).
  - `combine_ttm` carries the `ttm_ly` of whichever path it uses.
  - Instant path: `avg5_ly` is the average of the up to five balance-sheet dates ending at the
    known date nearest the latest date minus 365 days.
- `panel/fundamentals.py` adds `YEAR_AGO_FIELDS`: `<prefix>_ttm_ly` for revenue, operating_income,
  net_income, eps, da, ocf and capex, plus `equity_avg_ly`. They are float32 and age with their TTM
  value (the same `value_filed` freshness mask). `equity_avg_ly` is masked like `equity_avg`.
- `features/quality.py` uses the year-ago fields in place of `shift(year)`:
  - `revenue_growth`, `eps_growth`
  - `margin_change`, with `margin_ly = op_ly / rev_ly`
  - `fcf_prev = ocf_ly - capex_ly`
  - `roe_change`, with `roe_ly = ni_ly / equity_avg_ly`

  Gate 3, the sub-scores and the thesis stop all follow, since they use `trap_fails`.

### Tests

New tests:
- `test_quarter_ttm_ly_sums_the_four_quarters_a_year_before_q0`, a known YoY pair: TTM 100 vs 10
- `test_ttm_ly_never_compares_a_period_with_itself`
- `test_annual_ttm_ly_is_the_prior_fiscal_year`
- `test_combine_carries_the_year_ago_value_of_the_path_used`
- `test_instant_avg5_ly_averages_the_dates_ending_a_year_earlier`
- `test_year_ago_steps_truncation_invariant`
- `test_yoy_growth_pairs_the_ttm_with_the_four_quarters_a_year_earlier`, at database level. Filings
  stop, and a year later growth is 62/46-1, not 0.
- `test_year_ago_values_come_from_the_year_ago_fields_not_a_shifted_panel`

Updated quality tests (they built shifted panels; they now set `_ly` fields):
- `test_growth_and_margin_change_compare_with_a_year_earlier`
- `test_fcf_score_levels`
- `test_fcf_score_middle_levels_and_unknown_prior_year`
- `test_known_values_for_eps_roe_and_margin_scores`
- `test_quality_is_truncation_invariant`, which gains `_ly` inputs

RED, the new database-level test against the I1 commit: `E KeyError: 'revenue_ttm_ly'` (1 failed).

GREEN:
```
tests/panel/test_steps.py: 43 passed; tests/panel/test_fundamentals.py: 16 passed
uv run pytest -q: 687 passed, 15 deselected in 39.68s
uv run ruff check src tests: All checks passed!
realdb (data/realdb-trunc-i2.log): fundamental cells differing 0 at all three cuts; 1 passed in 150.69s
```

---

## I3 / R35: reference ETF missing early (9c5f2cc)

### Change

- `features/dislocation.py` gains three functions:
  - `reference_choice(etf_adj, ref_of, columns, days)`: per day and stock, the position of the
    first ETF in the stock's chain (industry ETF, sector ETF, SPY) that has a `days`-day return
    that day, or -1 when none has. A plain string mapping still works: that ETF, then SPY.
  - `take(frame, choice)`
  - `reference_returns(..., choice)`

  The `Dislocation` dataclass gains `ref_choice`, and the gap uses that day's choice.
- `features/flow.py`: `flow` computes the same choice with `high_window`, so it matches the
  dislocation choice on the same ETF panel. `active_etfs(eligible, choice, etfs)` counts an ETF as
  in use on a day when it is the reference of an eligible stock that day. `stock_codes(codes,
  choice)` takes that day's ETF's code.
- `signals/evaluate.py`:
  - `reference_chains(entities)` builds `(industry_etf, sector_etf)` per symbol.
  - `rows["ref_etf"]` and `dislocated_all["ref_etf"]` are that day's ETF.
  - `Evaluation.ref_fallback` is a bool panel: the chosen ETF is not the chain's first.
- `research/outcomes.py` `forward_returns` uses the event's `ref_etf` when the column exists, so
  `xs_ref` follows the same per-day choice.
- `research/coverage.py` adds `ref_fallback_days` with `member` and `eligible` counts.
  `cli.py` prints them.

### Tests

New tests:
- `test_reference_falls_back_until_a_late_etf_has_its_window`: XLRE lists on day 100; XLF is used
  until day 163, then XLRE; an empty chain uses SPY; the gap is checked; truncation-invariant
- `test_stock_codes_follow_the_days_reference_etf`
- `test_flow_tag_falls_back_with_the_reference_etf`: five ETFs in use; before XLRE's window, A's
  tag equals the run mapped straight to XLF
- `test_xs_ref_uses_the_events_own_reference_etf`

Updated tests:
- `test_active_etfs`, for the new signature
- `test_coverage_on_the_fixture`, which also asserts `ref_fallback_days` cell by cell

GREEN:
```
tests/features: 66 passed; tests/features/test_flow.py tests/research/test_outcomes.py: 28 passed
uv run pytest -q: 691 passed, 15 deselected in 39.11s
uv run ruff check src tests: All checks passed!
realdb (data/realdb-trunc-i3.log): all three cuts 0 differences; 1 passed in 148.93s
```

---

## I4 / R36: regime unknown everywhere (c11d7e5)

### Change

- `rules.toml` `[regime]` adds `min_known = 5`, with a comment on the share rule. The version is
  now `2026.10.24-4`. `config.RegimeRules` gains `min_known`.
- `features/regime.py`:
  - `load_macro` adds `<series>_age`: calendar days since the forward-filled value became known
    (its date plus the publication lag).
  - `MAX_AGE_DAYS = {daily 5, weekly 21, monthly 93}`. A stale value makes its indicator unknown.
  - The values themselves are still forward-filled. Blanking them would cut the 756-day rolling
    medians for three years after any gap.
  - Classification uses the indicators known that day when at least `min_known` are known:
    - loose when `6*loose >= loose_min*known`, which equals `loose >= ceil(4/6*known)`
    - tight when `6*loose <= tight_max*known`
    - otherwise neutral

    With six known this matches the old 4/2 rule.
- `research/run.py`:
  - Every event gets the regime of its day, which dislocated_all events did not carry before.
  - The summary adds `regime_known_share` per group (in-period events), and the CLI prints it.

### Tests

- New regime tests:
  - `test_one_missing_series_classifies_from_the_other_five`: tight, tight, neutral, neutral,
    loose, loose for 0 to 5 loose
  - `test_two_missing_series_make_the_regime_unknown`
  - `test_series_with_no_rows_is_left_out`
  - `test_a_stale_value_is_unknown_after_three_cadences`
  - `test_stale_series_drops_out_of_the_count`
- Replaced tests, which encoded the old "one unknown means unknown" behaviour:
  `test_one_missing_series_makes_the_regime_unknown` and
  `test_series_with_no_rows_makes_the_regime_unknown`.
- `test_regime_rules_load` asserts `min_known`.
- `test_in_sample_run_writes_outputs` checks `regime_known_share` against the events file.

RED: there was no separate RED run for the new tests. I implemented first. The two old tests then
failed as expected (`E - unknown / E + neutral`: a frame with one missing series is now
classified), and I replaced them with tests for the ruled behaviour.

GREEN:
```
tests/features/test_regime.py tests/test_config.py: 31 passed in 0.45s
uv run pytest -q: 694 passed, 15 deselected in 39.06s
uv run ruff check src tests: All checks passed!
```

Real data, regime by year on trading days:
- 2009-2011: all unknown (the reverse-repo series is sparse and the 3-year medians need history)
- 2012: 16 loose, 234 unknown
- 2013: 16 loose, 72 neutral, 164 unknown
- 2014 onwards: always known, loose or neutral, never tight

---

## I5 / R37: OOS guard and log bypasses (5e2c98f)

### Change

- `config.parse_rules(text)` is factored out of `load_rules`.
- `research/run.committed_research_rules()` parses `git show HEAD:rules.toml` and raises
  `GitError` when git cannot answer.
- The CLI refuses a `--rules` file whose `ResearchRules` differ from the committed ones. It exits
  2 with "the [research] section of --rules differs from the committed rules.toml; ...". A git
  failure also exits 2.
- `run_id_for(now, period)`. `append_oos_started(...)` writes a row with verdict `started` and
  empty metrics; `append_oos_log` shares the writer. For `--period oos`, the CLI appends the
  started row before `run_research` (passing the same `now`, so the run_id matches) and the final
  row after. `OOS_FIELDS` is unchanged.

### Tests

New tests:
- `test_cli_refuses_rules_whose_research_section_differs` (oos_start moved to 2026; no output
  written)
- `test_committed_research_rules_need_git`
- `test_cli_oos_crash_after_start_leaves_the_started_row`. `run_research` is patched to raise; the
  started row is asserted present before the crash and as the only row after. The real log is
  byte-identical.

Updated tests:
- `test_cli_oos_success_logs_before_printing`: rows are now `started`, then `fail`, with one
  run_id.
- `tests/research/test_end_to_end.py`: the tuning copy changed `panel_start` (a `[research]` key),
  which the new guard refuses. It now changes `min_drawdown` 0.15 to 0.149 instead. The fixture
  data starts in 2012, so the panel dates are the same either way.

RED, the refusal test against the I4 commit:
```
E       assert (0 == 2)
E        +  where 0 = <Result okay>.exit_code
1 failed, 11 deselected in 5.26s
```

GREEN:
```
tests/research: 48 passed in 25.60s
uv run pytest -q: 697 passed, 15 deselected in 39.24s
uv run ruff check src tests: All checks passed!
```

---

## M1 (bf14a59)

- `BREAKDOWN_COLUMNS` now holds `shock, correction, sector, flow, ref_etf, regime, variant, tier,
  annual, fallback, has_missing`.
- Events get `flow`, mapped from evaluate's `industry` code: 1 is hot, 0 is neutral, -1 is cold,
  and NaN or absent is unknown. Evaluate's rows keep the column name `industry`.
- Test: `test_in_sample_run_writes_outputs` asserts that breakdowns.csv has `flow` and `ref_etf`
  and no `industry`, and that the flow labels map from the code.
- Output: `uv run pytest -q: 697 passed, 15 deselected in 39.32s`; ruff `All checks passed!`

## M4 (ba96f20)

- `gates.value_trap`: the revenue rule is known when TTM growth is known and either the quarter
  comparisons are known or growth >= 0.
- New test: `test_revenue_rule_is_known_not_fired_when_growth_is_not_negative`.
- RED:
  ```
  E       assert [True, True, True] == [False, False, True]
  ```
- GREEN: `10 passed`; `uv run pytest -q: 698 passed, 15 deselected in 39.32s`; ruff clean.

## M5 (8572960)

- I re-ran the in-sample research on the real database after all the code fixes. No update was
  run and no `--period oos`. The run used `/usr/bin/time -v` in the background, with logs
  `data/phase2-research-final.log` and `data/research-time-final.txt`.
- I updated the phase-2 status section of `docs/plans/2026-10-10-phase-1-status.md` with:
  - the new numbers and both verdict lines
  - the notes: setup n is in-sample events only; S&P 400 starts 2013 and 2009 has no members;
    regime known-share; ETF-fallback days
  - the real-database truncation result

### In-sample numbers, old vs new

| | First run (rules -3) | After fix wave (rules -4) |
|---|---|---|
| setup n | 436 | 441 |
| dislocated n | 3787 | 3865 |
| dislocated_all n | 4726 | 4843 |
| setup median 63d excess vs SPY | -1.15% | -1.09% |
| setup win rate | 48.2% | 48.5% |
| dislocated median | -0.10% | -0.03% |
| dislocated_all median | -0.32% | -0.29% |
| tests | n PASS, median FAIL, win_rate FAIL, beats_dislocated FAIL, quintiles PASS | n PASS, median FAIL, win_rate FAIL, beats_dislocated FAIL, quintiles PASS |
| verdict | FAIL (in-sample: tuning only) | FAIL (in-sample: tuning only) |
| wall clock | 8:16 | 9:44 |
| peak memory | 2,339,192 kB (2.2 GiB) | 2,691,792 kB (2.6 GiB) |

Other results:
- Regime known on 100% of in-period event days in every group. Setup events: 154 loose, 287
  neutral.
- Reference-ETF fallback ticker-days: 65,085 member days and 27,405 eligible days.
- Plan outcomes:
  - swing: 170 time, 106 stop, 93 target, 72 no_fill
  - position: 169 stop, 96 target, 72 no_fill, 52 time, 52 thesis

Raw tail of `data/phase2-research-final.log`, INFO lines removed:
```
IN-SAMPLE (tuning only)
guidance_tone_enabled=False win_basis=excess_spy
coverage 2009: 0/0 members with prices (nan%)
coverage 2021: 814/956 members with prices (85%)
excluded by fate: {'acquired': 145, 'other': 108, 'recycled': 31, 'unknown': 227}
aliases: {'cik': 50}
skipped ticker-days by reason: {'variant': 23306, 'history': 273707, 'required': 200271, 'market_cap': 8679}
reference-ETF fallback ticker-days: {'member': 65085, 'eligible': 27405}
event days with a known regime: setup 100%, dislocated 100%, dislocated_all 100%
setup           n=441    median 63d excess vs SPY=-1.09% win rate=48.5%
dislocated      n=3865   median 63d excess vs SPY=-0.03% win rate=49.9%
dislocated_all  n=4843   median 63d excess vs SPY=-0.29% win rate=49.2%
  PASS n
  FAIL median_xs_spy
  FAIL win_rate
  FAIL beats_dislocated
  PASS quintiles
verdict: FAIL (in-sample: tuning only)
report: /home/pol/polalejandre/stock-analyzer/data/research/20261024-140642-in_sample
research exit 0
	Elapsed (wall clock) time (h:mm:ss or m:ss): 9:44.12
	Maximum resident set size (kbytes): 2691792
	Exit status: 0
```

Final suite after the last commit:
```
uv run pytest -q
698 passed, 15 deselected in 37.76s
uv run ruff check src tests
All checks passed!
```

---

## Deviations and concerns

1. **I1 scope widened.** Besides the ruled direct/derived change, I1 also fixes the predecessor-CIK
   filter in `load_concept(all_versions=True)` (AVGO), first-filed chain links (RBC) and a total
   tie order (SUN). The real-database test failed on these after the ruled change alone. Each is
   the same "no value before its filed date / truncation invariance" rule, so I did not treat them
   as a design change. The predecessor filter also changes what `load_quarters` (the audit) sees.
   The phase-1 test that asserted the old behaviour was updated, because it had asserted a
   lookahead.
2. **I3 flow side effect.** When a stock's reference switches to a newly listed ETF (once it has
   63 days), its flow tag is unknown until that ETF also has the 126-day RS window. This follows
   the "same per-day fallback" ruling. The dislocation gap uses the new ETF at once. See the last
   assertion of `test_flow_tag_falls_back_with_the_reference_etf`.
3. **I4 staleness.** Age is measured from when a value became known (date plus lag), not from its
   observation date, and only gates the indicator's "known" flag. On real data the regime is never
   "tight" in 2014-2021, with the high-yield series absent and five indicators.
4. **I5 guard.** The guard compares against `git show HEAD:rules.toml` and applies only to
   `--rules`. An uncommitted edit of `[research]` in the working `rules.toml` itself, run without
   `--rules`, is still not caught for in-sample runs. That was outside the ruling.
5. **Dead config key.** `features.year_days` is no longer used by the engine. It is still in
   `rules.toml` and `FeatureRules`, and only a test reads it. I left it rather than change config
   twice.
6. **Run time.** The in-sample run is now 9:44, against the 10-minute target, up from 8:16.
   Peak memory is 2.6 GiB, up from 2.2 GiB.
