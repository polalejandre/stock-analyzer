Global constraints (verbatim from the plan's Global Constraints):
- Python >=3.12. No new dependencies in pyproject.toml.
- Free data only (spec non-goal "Paid data").
- A fundamental value becomes known on its EDGAR `filed` date, never its period-end date (spec 3.3).
- Every threshold and weight lives in rules.toml. Bump `version` whenever any value changes.
- guidance_tone_enabled = false for the first go/no-go run (addendum 2.3).
- In-sample: events from 2014-01-01 whose 126-day forward window ends before 2022-01-01. Out-of-sample: events from 2022-01-01 with a full 63-day window (addendum 6.3).
- 42-trading-day cooldown per ticker per group (addendum 6.2).
- Signal at the close of day t; entry at the open of day t + 1 (addendum 6.2).
- Go/no-go (spec 9.6): group 3 n >= 100; median 63-day excess return vs SPY > 0; win rate >= 55% at 63 days; median 63-day excess return above group 2's; top score quintile above bottom quintile.
- Default tests never touch the network (autouse guard in tests/conftest.py). Live tests are marked @pytest.mark.live.
- Dates are ISO YYYY-MM-DD strings in SQLite and datetime64[ns] in pandas.
- Canonical ticker form: uppercase, '.' replaced by '-'.
- Wide panels: index = trading dates (DatetimeIndex, from SPY bars), columns = price symbols. Fundamental panels are float32 to bound memory.
- data/ is gitignored. Research outputs go to data/research/<run-id>/; only docs/research/oos-runs.csv is committed.
- Every feature must be truncation-invariant: its value at date d is identical whether or not data after d exists (addendum 4.5).
Controller rulings binding on reviewers: R5 — plan code is a starting point; implementers may fix plan-code bugs that make the plan's own tests fail, and must report each.
- Required commit trailer (controller attribution instruction for this session): "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>". Subagents may see a different model name in their own reminders; this one is binding.
