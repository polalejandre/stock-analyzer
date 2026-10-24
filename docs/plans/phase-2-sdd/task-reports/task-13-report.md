# Task 13 report
Implemented src/analyzer/features/flow.py verbatim from brief; tests/features/test_flow.py from brief.
RED: `uv run pytest tests/features/test_flow.py -q` -> ModuleNotFoundError: No module named 'analyzer.features.flow' (collection error).
GREEN: same command -> 4 passed. Full suite: 573 passed; ruff clean.
Deviation: test helper lambda `grow` rewritten as a def (ruff E731). No other changes.

## Fix round 1
Changes: rules.toml version 2026.10.24-2 + flow_min_industries = 5; FeatureRules.flow_min_industries; etf_flow_codes now ranks by method="min" rank <= n*(1-hot_rank) (hot, with trend) / n*cold_rank (cold), eps 1e-9 to avoid float noise (1-0.8 != 0.2 in floats), all NaN when n < flow_min_industries; up/down days from adj_close.diff(); NaN ratio scores 0.5 (inf still clips to 1).
Tests: 5 ETFs -> one hot, one cold, E1 neutral even with rising volume; 4 active -> all NaN; 10 ETFs -> 2 hot, 2 cold; exact updown_ratio (2.0) and accumulation (0.5); OBV divergence case True; no-moves case ratio NaN -> accumulation 0.25 (0.5 ratio part).
Commands: `uv run pytest tests/features tests/test_config.py -q` and full suite pass (all green); ruff clean.
