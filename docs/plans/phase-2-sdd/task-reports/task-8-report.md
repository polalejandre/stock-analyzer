# Task 8 report

Implemented `src/analyzer/panel/fundamentals.py` (brief code + rulings) and `tests/panel/test_fundamentals.py` (7 brief tests + truncation-invariance test).

RED: `uv run pytest tests/panel/test_fundamentals.py -q` -> ModuleNotFoundError: No module named 'analyzer.panel.fundamentals' (collection error).
GREEN: 8 passed; full suite 517 passed; ruff clean.
Mutation check: using raw `f` in `discrete_quarters` makes the truncation test fail.

Adaptations / rulings applied:
- R11: `first_versions(f)` feeds `discrete_quarters` and `annual_steps` (bank annual base too); NII and noninterest facts wrapped in `first_versions`. `fallback_ends` uses raw `f`.
- Debt: `instant_steps(total_debt(...), keep_updates=True)`.
- Import `first_versions` added; unused `facts`-style imports/`pytest` import removed by ruff.
- Truncation test: cut DB copies (facts filtered `filed <= d`, splits, tickers copied), compares `.frames` via `assert_truncation_invariant` (helper's `_frames` would otherwise include static Series without a date index, so compute returns only `.frames`).
Concern/note: `first_filed`, `has_property`, `has_us_gaap` are static and computed from all facts (not truncation-invariant by design, per brief).

## Fix round 1 (R14, R15)
1. `combine_ttm` now outputs `q_filed` (TTM_COLUMNS updated); `revenue_q*` age on it. Tests: reviewer scenario (`test_quarter_fields_age_on_their_own_filing`, NaN q fields on 2024-03-01, ttm still 50), `test_combine_reports_the_filing_of_the_quarter_step_used`.
2. `instant_steps` outputs `value_filed` (filed of the latest period end's latest update); equity/cash/debt age on it. Tests: `test_instant_fields_age_on_their_own_filing`, `test_instant_value_filed_ignores_older_dates_first_reported_later`.
3. Diluted counts split-adjusted by `filed` (cover keeps `period_end`). Test: `test_diluted_shares_not_double_adjusted_for_split_before_filing`.
4. One warning with count and first 5 symbols for failed companies. Test: `test_failed_companies_are_counted_in_one_warning`.
5. Truncation test now includes noninterest_income (and longer NII series); added `test_summed_fallback_first_report_sets_flag_from_its_filing` (flag True from first summed filing, 2020-05-01, which precedes the first TTM) and `test_equity_avg_is_mean_of_recent_balance_sheets`.
R15: module docstring states first_filed/has_property/has_us_gaap come from all facts and why.
Commands: `uv run pytest tests/panel -v` 55 passed; full suite passed; `ruff check src tests` clean.
