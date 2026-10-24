# Task 16 report: Variants

Status: DONE
Commit: a3b16fd feat: scoring variants per symbol

## Implemented
- `src/analyzer/signals/__init__.py` (empty)
- `src/analyzer/signals/variants.py`: `STANDARD`, `FINANCIALS`, `REIT`, `SCORED`, `variant_of`, `variant_series`, `columns_for_metrics`, verbatim from the brief.
- `tests/signals/__init__.py` (empty)
- `tests/signals/test_variants.py`: the two brief tests plus `test_variant_series_from_entities_and_static_panels`, which builds a small entities frame (cik float/NaN, sic str/None) and a `FundamentalPanels` with static `has_property` / `has_us_gaap` series, and asserts the five-symbol mapping (standard, no_cik, reit, mortgage_reit, no_us_gaap).

## TDD evidence
RED: `uv run pytest tests/signals/test_variants.py -v`
```
collected 0 items / 1 error
E   ModuleNotFoundError: No module named 'analyzer.signals.variants'
ERROR tests/signals/test_variants.py
```
GREEN: `uv run pytest tests/signals/test_variants.py -v`
```
test_variant_rules PASSED
test_columns_for_metrics PASSED
test_variant_series_from_entities_and_static_panels PASSED
3 passed
```
Full suite: `uv run pytest -q` -> 594 passed, 14 deselected. `uv run ruff check src tests` -> All checks passed.

## Deviations from brief
None to the code. The brief's code ran as written. The added test covers `variant_series`, which the brief did not test.

## Concerns
None blocking. `variant_series` uses `.get(s, False)` on the static series, so a symbol missing from the panels defaults to no property / no US GAAP, which yields `excluded:no_us_gaap` rather than an error.
