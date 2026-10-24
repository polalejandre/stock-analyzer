# Task 7 report

Implemented src/analyzer/panel/steps.py and tests/panel/test_steps.py verbatim from the brief.

RED: `uv run pytest tests/panel/test_steps.py -q` -> ERROR collecting: ModuleNotFoundError: No module named 'analyzer.panel.steps' (1 error).
GREEN: same command -> 10 passed. Full suite: 490 passed, 14 deselected (also clean with -W error::FutureWarning). `ruff check src tests`: all checks passed.

Deviations: none; brief code needed no fixes. No existing tests changed.
Concern (minor): _consecutive reuses QUARTER_DAYS (77,120, defined as inclusive period length) as the allowed gap between consecutive period ends; works as intended (~91 days), just semantic reuse.

## Fix round 1

Every builder now has a truncation test (inputs filtered to filed <= d give the full result's rows with filed <= d). Added 18 tests (28 in test_steps.py).

- C1: combine_ttm empty branches are built from `_empty` dtypes (period_end/a_end/filed datetime64, fallback boolean). Tests: annual-only (annual=True, ttm=120), quarters-only, both empty.
- I2: total_debt emits a row at each component's first filing (long line alone, then the sum; same when the short line is first). Overlap (LongTermDebt + current-portion tag) ignores the short line and does not delay `filed`. Design: output keeps two rows for a date; `instant_steps(facts, keep_updates=False)` gained `keep_updates=True`, which skips the first-report dedup and lets rows update a date in filing order. Debt callers (Task 8) must pass `keep_updates=True`. Chosen as simplest: other instant concepts keep the ignore-restatements rule.
- I3: add_missing_periods keeps an extra row when its filed is strictly earlier than every primary row within SAME_QUARTER_DAYS. DEVIATION: it does not snap period_end. Snapping depends on primary rows filed after the extra row, so a result cut before the primary arrives would differ from the full one (breaks truncation invariance). Instead quarter_steps treats ends within SAME_QUARTER_DAYS as one key (newest end replaces the old key), which gives the same effect: the later primary row replaces the value at its own filing. Tested with the 2018/2019 repro and a one-day-off end.
- I4: fallback_ends looks only at first_versions (earliest filed per period_start/period_end, ties by accession). Tests: primary first + later fallback not flagged; fallback first flagged; truncation.
- I5: first_versions added; tests for the 2023-05-01 vs 2024-02-15 re-report and truncation invariance of quarter_steps(discrete_quarters(first_versions(f))).
- R12: combine_ttm prefers annual only when a_end > q_end + SAME_QUARTER_DAYS (test with an annual ending 3 days later). latest_value_steps sorts by [filed, accession], keeps the shortest duration per period end (input-order test).

Commands: `uv run pytest tests/panel -v` -> 38 passed; `uv run pytest -q` -> 508 passed, 14 deselected; `uv run ruff check src tests` -> clean (after import sort fix).

## Fix round 2

I2 overlap order: in total_debt's overlap branch (LongTermDebt + current-portion tag), when the short line is filed before the long line the short row (value_s, filed_s) is now emitted first, then (value_l, filed_l) replaces it. Test `test_debt_overlap_with_short_line_first_is_truncation_invariant`: exact rows [10 @2020-02-15, 100 @2021-02-15] and invariance at cuts 2020-02-15, 2020-06-01, 2021-02-15.
Commands: `uv run pytest tests/panel -q` -> 39 passed; `uv run pytest -q` -> 509 passed, 14 deselected; `uv run ruff check src tests` -> clean.
