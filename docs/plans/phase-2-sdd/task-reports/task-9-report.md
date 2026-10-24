# Task 9 report: value features

Implemented src/analyzer/features/{__init__,value}.py and tests/features/{__init__,test_value}.py. Brief code used verbatim; no fixes needed. Added one test beyond the brief: test_peer_median_is_truncation_invariant (NaNs, random eligibility, industry/sector fallback).

RED: `uv run pytest tests/features/test_value.py -q` -> ModuleNotFoundError: No module named 'analyzer.features.value' (collection error).
GREEN: same command -> 6 passed. Full: `uv run pytest -q` -> 531 passed, 14 deselected; `uv run ruff check src tests` -> All checks passed.

Concern: in value_features the comment says peers span every eligible stock using the metric, but the code passes x (only columns_for[metric]) to peer_median, so peer medians are computed over just those columns. Left as in brief; caller should pass columns_for covering the full universe or this should be revisited.

## Fix round 1
- Comment and value_features docstring now state R16 (peers = eligible symbols in columns_for[metric]); behaviour unchanged.
- Added `ebitda(f)` helper; `market_cap`/`ebitda` used everywhere (no duplicate formulas).
- `ratios(close, f, columns_for=None)` computes each metric only for its columns (skips empty metrics); value_features passes it.
- New tests: value_features columns/skip/shape/known values (pct, p50, peer, market_cap, ebitda, net_debt), net_debt NaN without equity, EV<=0 -> NaN EV/EBITDA, ratios(columns_for) == full subset, value_features truncation invariance over all frames. Fixtures use a SimpleNamespace for rules (FeatureRules has 30+ required fields).
- Commands: `uv run pytest tests/features -v` -> 10 passed; `uv run pytest -q` -> 535 passed, 14 deselected; `uv run ruff check src tests` -> All checks passed.
