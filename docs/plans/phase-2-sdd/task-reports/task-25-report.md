# Task 25 report
Implemented run.py (run_research, git_state, append_oos_log, PERIODS/FROZEN_PATHS/OOS_LOG), header-only docs/research/oos-runs.csv, `analyzer research run` CLI, tests/research/test_run.py.

RED: `uv run pytest tests/research/test_run.py -q` -> ImportError: cannot import name 'run' from 'analyzer.research' (collection error).
GREEN: same command -> 4 passed. Full suite: 665 passed, 14 deselected; ruff clean.

Adaptations: brief code matched the real APIs (entry_price, delisted_h, coverage(conn, panels, evaluation), simulate_plans no_plan) with no code changes; only the test's allowed swing_outcome set adds "delisted" and "no_data". Fixture-based tests use temp dirs; OOS log test writes to tmp_path; CLI guard tests exit before loading settings/DB, so no real data or oos-runs.csv is touched. OOS guard checks rules.toml and the addendum via git_state before any config load.
Concerns: "no_plan" is not in the test's allowed set (DIP gets plans, so not hit). Verdict `tests` for in-sample is labelled tuning-only.

## Fix round 1
- R30: in-sample `run_research` loads panels with end = oos_start - 1 day (or slices passed panels with `upto`), asserts no oos/oos_incomplete periods. Test test_in_sample_never_reads_oos_prices (oos_start moved to 2016-06-01; no event dated on/after it, no oos labels, panel end before it, both passed-in and self-loaded panels).
- R31.1 CLI prints guidance_tone/win_basis, first/last-year coverage, excluded by fate, aliases, skipped ticker-days; "IN-SAMPLE (tuning only)" header. R31.2 oos log appended before printing. R31.3 append_oos_log(path=None) resolves OOS_LOG at call time; CLI passes research.OOS_LOG. R31.4 git_state returns (commit, frozen_dirty, dirty_tree), raises GitError on git failure (CLI exits 2 via _fail), untracked frozen file counts dirty (ls-files --error-unmatch); dirty_tree column added to OOS_FIELDS and the committed header.
- R31.5 tests: temp-repo git_state (clean, modified, staged, deleted, untracked, dirty_tree, outside repo), CLI git failure, CLI oos success path (tmp log, tmp data dir, real log bytes unchanged, log line precedes verdict).
- Commands: `uv run pytest tests/research -q` -> 43 passed; `uv run pytest -q` and `uv run ruff check src tests` (see final run in commit step).
