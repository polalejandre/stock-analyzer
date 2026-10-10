# Phase 1 status and resume notes (2026-10-10)

Branch `phase-1-data-layer`. Nothing is pushed or merged into `main` yet.
Plan: `docs/plans/2026-10-10-phase-1-data-layer.md`. Spec: `docs/specs/2026-10-10-stock-analyzer-design.md`.
The detailed ledger (every ruling, review and deferred item) is in
`.superpowers/sdd/2026-10-10-phase-1-data-layer/progress.md`. That folder is gitignored, so keep
it until phase 1 is closed.

## Where we are

- All 17 plan tasks are implemented, reviewed and merged into the phase branch. The final
  whole-branch review and its fix wave are done. 408 unit tests pass and ruff is clean.
- Follow-ups after the final review (owner approved):
  - A Wikipedia snapshot month counts as failed only in a run where today's page succeeded,
    and at most once per day. Before this, an outage across 3 runs gave up on every month not
    yet stored (commit bc8f9ac).
  - A price history and its split events are replaced in one transaction (58fc232).
- Task 17B, the live run, is in progress:
  - `.env` is set up: SEC_USER_AGENT with the owner's email, and FRED_API_KEY.
  - Live tests: `uv run pytest -m live -s -v` gives 14/14 passing.
    - The first run found a real bug: SEC's daily index header says "File Name", not
      "Filename", so every daily index was rejected. Fixed in 1cec207.
    - Three checks were rewritten to match what SEC's data really holds (92dccaf). See "Known
      data gaps".
  - The first full `uv run analyzer update` started 2026-10-10 22:24. Log:
    `data/logs/first-update.log`.
    - The universe step finished at 23:04: 352 of 371 snapshot fetches worked. The other 19
      months got HTTP 429 from Wikipedia; they are retried on the next runs.
      - S&P 500: 195 snapshots, 2010-01 to today.
      - S&P 400: 156 snapshots, 2013-04 to today.
    - The prices step was running when we stopped: 1,085 symbols and 4.2M bars by 23:30.
      After it come the EDGAR and macro steps.

## Next steps (resume here)

1. Check whether the first update finished:
   - `tail -50 data/logs/first-update.log`
   - Look for the `step <name>: N/M failed` lines and the final summary.
   - If it was interrupted, run `uv run analyzer update` again. It resumes: stored prices,
     the cache and the pointers are kept.
2. Run `uv run analyzer audit`. Record the coverage numbers here:
   - tickers with prices;
   - CIKs mapped;
   - fundamentals coverage;
   - insider rows;
   - FRED series.
3. Fix any real-world mismatches the audit shows. Then run `analyzer update` again to fill the
   Wikipedia months that got 429.
4. Close phase 1: merge `phase-1-data-layer` into `main` (needs the owner's OK), then delete the
   SDD workspace.
5. Optional, recommended: a throwaway preview page built from the stored data. It would list:
   - drawdown from high;
   - basic valuation;
   - sector money-flow ranking.

   It is labeled a preview and is not the phase-2 scoring.
6. Phase 2: write the plan for features, signals and the research harness. It ends with the
   go/no-go of spec section 9.6. Include the phase-2 prep items below.

## Open decision for the owner

- Wikipedia User-Agent contact: none at the moment. Wikipedia asks for a contact, which would
  likely mean fewer 429s. The owner's email is already used for SEC. Owner to decide.

## Known data gaps (found by the live run)

- **NVDA capex:**
  - Q4 of FY2016 to FY2020 and Q1–Q2 of FY2023 can't be derived. NVDA tagged those periods
    with a company-specific extension tag, and companyfacts only has standard tags.
  - Quarters are continuous since FY2024.
- **XOM:**
  - Now files under CIK 2115436 (ExxonMobil Holdings). The merge with the old CIK 34088 is
    needed, and it works.
  - Quarterly revenue before 2017 is missing (extension tag); only annual revenue exists for
    2009–2016.
- **BRK-B:**
  - Berkshire reports share counts only per class, as dimensional facts, which companyfacts
    leaves out. The newest plain count is from 2011.
  - So BRK-B has no market cap. That is harmless in phase 1, because market cap only gates
    young listings.
- **Delisted and renamed past members:**
  - Yahoo has no prices for many acquired or delisted names (e.g. ATVI, AGN, ALXN, AET, PXD,
    STJ). They are logged and retried after 30 days.
  - Renamed tickers (ANTM→ELV, ABC→COR) also miss.
  - This survivorship gap must be handled in the phase-2 research.

## Phase-2 prep items (deferred, from reviews and the live run)

- **Fundamentals:**
  - Build TTM values from annual 10-K figures when quarters are missing (NVDA and XOM cases).
  - Read per-class share counts from the XBRL instance (dimensional dei); for BRK, Class A =
    1,500 Class B.
  - Measure across the universe how often the cover-page share count is stale.
  - Fix the debt-tag double count.
- **Survivorship and identity:**
  - Young-listing survivorship (I2).
  - Ticker identity via the snapshot CIK plus an alias table, for renames like FB→META and
    ANTM→ELV (I5).
  - Price coverage of delisted past members (consider another source or an explicit
    exclusion in the research).
- **Macro:** FRED values as first published, via ALFRED vintages (I4).
- **Code:**
  - Read-side provider interfaces (M2).
  - Insider form column with per-owner roles.
- Phase-3 operational items are listed in the ledger.
