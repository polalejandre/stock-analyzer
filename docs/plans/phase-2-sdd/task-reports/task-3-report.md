# Task 3 report

Implemented aliases.py, aliases.toml, registry snapshot-CIK fallback, sync_aliases at end of step_universe. Brief code used verbatim (only change: `.fetchall()` added in change_reasons so the cursor is not iterated while another statement runs; harmless).

## RED
`uv run pytest tests/universe/test_aliases.py tests/universe/test_registry.py`:
- ModuleNotFoundError: No module named 'analyzer.universe.aliases'
- registry test alone: `assert (None,) == (4242,)` (1 failed, 2 passed)

## GREEN
`uv run pytest -q`: 461 passed, 14 deselected. `ruff check src tests`: clean (one import-order autofix in the appended registry test).

## Existing tests updated (intended change: aliases item)
tests/pipeline/test_update.py: three step_universe tests: attempted +1 each, and detail strings now carry " aliases=0 recycled=0 excluded=0" (lines ~184, 211, 871). Fake wiki clients: sync_aliases found no unreadable-revision problems (no snapshot rows), no network hit.

## Concerns
Commit trailer follows implementer-rules (Claude Opus 5.5) rather than the session attribution reminder (Sonnet 5.5); amend if wanted.

## Fix round 1
- F1 (R6): build_aliases canonicalizes snapshot CIKs through load_cik_merges (new `merges_path` param, default merges.DEFAULT_PATH, built from the tickers table); >1 canonical company => `recycled` ("snapshot CIKs [..] name several companies") unless a manual alias exists. snapshot_ciks still returns the raw union.
- F3 (R7): alias stores the CIK that matched the target, not min(seen).
- F2: tests added: recycled for two companies, merge predecessor kept, manual beats recycled, alias CIK stored, backfill (fill / skip done / unreadable logged), change_reasons (latest per index, empty skipped), registry latest-snapshot CIK wins, step_universe alias failure recorded in failed.
- Command: `uv run pytest tests/universe tests/pipeline -q` -> 226 passed; full `uv run pytest -q` and `ruff check src tests` run before commit (output below in status).
