# Task 15 report: liquidity regime

Status: DONE
Commit: 7f2202a feat: liquidity regime with publication lags

## Implemented
- src/analyzer/features/regime.py: PUBLICATION_LAG_DAYS, load_macro (lagged, forward-filled per trading date), regime (loose/neutral/tight/unknown). Verbatim from brief.
- tests/features/test_regime.py: the brief's two tests verbatim, plus test_regime_is_truncation_invariant. The truncation test rebuilds a DB per cut date with macro rows dated <= end and trading dates <= end before calling load_macro and regime, and uses assert_truncation_invariant at two cut dates.

## TDD evidence
RED: `uv run pytest tests/features/test_regime.py -v` -> collection error, `ModuleNotFoundError: No module named 'analyzer.features.regime'`.
GREEN: `uv run pytest tests/features/test_regime.py -v` -> 3 passed.

## Verification
- `uv run pytest -q` -> 587 passed, 14 deselected.
- `uv run ruff check src tests` -> All checks passed.

## Deviations
- Ruff flagged `zip(dates, vals)` in the new truncation test (B905). Fixed with `strict=True`; both sides have length n.
- Commit trailer: the rules file says Co-Authored-By Claude Opus 5.5; the harness attribution reminder says Haiku 5.5. I used the rules-file trailer as the controller instruction specifies. Change it if the harness value is preferred.

## Files changed
- src/analyzer/features/regime.py (new)
- tests/features/test_regime.py (new)

## Concerns
- None blocking. The truncation test uses synthetic random-walk macro series; it checks causality of the rolling/lag logic, not real FRED data.

## Fix round 1 (controller ruling R23)

### Changes
1. Thresholds moved to rules.toml: new `[regime]` section (`loose_min = 4`, `tight_max = 2`, `median_window = 756`, `change_window = 65`, `yoy_window = 252`, each commented). `version` bumped to "2026.10.24-3".
   - config.py: frozen `RegimeRules` dataclass; `Rules.regime: RegimeRules` loaded with `_section`.
   - `regime(macro, rules: RegimeRules)` uses those values. `PUBLICATION_LAG_DAYS` stays a module constant, commented as FRED publication facts, not thresholds.
   - One literal remains: NFCI `< 0.0`. Zero is the NFCI definition of average conditions, not a tuned threshold.
2. Duplication removed: three helpers, each returning `(indicator, known)`:
   - `_below_threshold(x, value)` for NFCI.
   - `_below_rolling_median(x, window)` for HY spread and real yield.
   - `_moved_over(x, window, direction)` for WALCL, M2 and RRP. M2 uses the difference rather than the ratio; for a positive series the sign is the same.
   - Behaviour change: `known` for the change helper now also requires the current value to be non-NaN. Identical on the ffilled macro frame in practice; no test depends on the difference.
3. Tests:
   - Lag test strengthened: M2SL dated 2024-02-01, lag 58 gives Sat 2024-03-30. Asserts NaN on Fri 2024-03-29 and 100.0 on Mon 2024-04-01, and first valid is 2024-04-01.
   - Added: exactly 3 loose gives "neutral"; one NaN series (NFCI) gives "unknown" even when the other five say loose; a series with no rows at all (DFII10, via load_macro) gives "unknown".
   - Config test `test_regime_rules_load` added to tests/test_config.py.
   - Truncation test passes `RULES` through `regime`.

### Commands and output
- `uv run pytest tests/features/test_regime.py tests/test_config.py -v` -> 28 passed (6 regime, 22 config; includes test_regime_rules_load).
- `uv run pytest -q` -> 591 passed, 14 deselected.
- `uv run ruff check src tests` -> All checks passed!

### Files changed
- rules.toml, src/analyzer/config.py, src/analyzer/features/regime.py, tests/features/test_regime.py, tests/test_config.py
