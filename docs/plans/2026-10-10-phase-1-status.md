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
    - The owner asked to stop the run, so it was stopped at 23:34 with SIGTERM. The database
      integrity check is ok. Stored at that point: 1,085 symbols and 4.2M price bars, and all
      snapshots. Not run yet: the rest of the prices (from about "S"), the young-listing
      screen, EDGAR and macro. Expected remaining time: about 2–3 hours, mostly EDGAR.

## First full update and audit (2026-10-11)

The resumed update ran from 09:32 to 12:41 and exited cleanly. Log:
`data/logs/update-2026-10-11.log`; audit output: `data/logs/audit-2026-10-11.log`.

| Step | Attempted | Failed | Notes |
|---|---|---|---|
| universe | 22 | 1 | Today's S&P 400 page got HTTP 429; all 19 missed months were filled. |
| prices | 932 | 0 | 1,063 symbols topped up; 533 past members have no prices (delisted or renamed). |
| edgar | 3,561 | 0 | 1,779 companies; 44,548 Form 4 XML filings parsed. 2 instance fallbacks got an invalid body. |
| macro | 6 | 0 | |

Coverage after the run:

- Tickers: 2,358 (1,117 active, 1,241 inactive). 1,825 have prices (5.78M bars, 1,853 symbols
  including reference ETFs). Every reference ETF has prices.
- CIKs mapped: 1,794 of 2,358 tickers; 1,780 companies stored.
- Fundamentals: 1,526 tickers with 8+ revenue quarters; 1,292 with a recent FCF (four quarters
  of operating cash flow and capex). 3.86M facts; 1.35M filings. Companyfacts behind the
  latest 10-Q/10-K: 7.
- Insider rows: 1.82M for 1,756 issuers, 2013-01-02 to 2026-10-09.
- FRED: all 6 series, through 2026-10-09 at the latest. BAMLH0A0HYM2 (high-yield spread)
  starts only at 2023-10-10, because FRED now publishes just 3 years of the ICE BofA series.
- Audit flags on active tickers: 379. By flag: no FCF 174, check CIK merge 140, no
  fundamentals 87, no FCF (financials, expected) 7, companyfacts behind 4, no sector ETF 3.
  The root-cause analysis of these flags is in "Audit findings" below.

## Audit findings (2026-10-11)

Root causes of the 379 flags, from the stored facts and the cached companyfacts files:

1. **Capex tags we don't map (no FCF, about 100 tickers).** Operating cash flow is present for
   all 181 "no FCF" tickers; capex is what is missing. Recent capex sits under tags outside the
   map: PaymentsForCapitalImprovements (26, mostly REITs), PaymentsToAcquireOtherPropertyPlantAndEquipment
   (14, e.g. LLY, EOG), PaymentsToAcquireOtherProductiveAssets (11, e.g. VZ),
   PaymentsToDevelopRealEstateAssets (11, REITs), PaymentsForProceedsFromProductiveAssets (7,
   net of sale proceeds), PaymentsToAcquireRealEstate (6), oil-and-gas development tags (5).
   Some of these are components, not totals, so each needs a rule, not just a list entry.
   40 more (e.g. COP, PSX, NEE, URI, ABNB) report capex only under company extension tags.
2. **Revenue tags we don't map (check CIK merge, about 60 tickers).** Before ASC 606 (2018),
   many companies tagged revenue as SalesRevenueGoodsNet (45) or SalesRevenueServicesNet (17).
   These are components: a company reporting both needs their sum.
3. **Real CIK reorganizations (check CIK merge, about 40 tickers).** New holding companies with
   a new CIK, e.g. GOOG/GOOGL (Alphabet 2015), MDT (2015), AVGO (2018), LIN (2018), CI (2018),
   DIS (2019), VTRS (2020), SW (2024), STE, ETN. Each needs a verified entry in
   `cik_merges.toml`.
4. **Quarters missing from companyfacts (no FCF, about 57 tickers).** The tag is mapped but
   companyfacts lacks some 10-Q periods: IDA has no Q1 2026 value; CHD has no 9-month 2025
   value. Young listings with fewer than 4 quarters filed are in this group too and are expected.
5. **Q4 derived from mixed vintages.** BAX: the FY2023 capex is a later restatement (432M,
   filed 2026) but the 9-month value is the original (502M, filed 2023), so the derived Q4 is
   -70M. The derivation must not mix a restated annual value with original year-to-date values.
6. **Non-stocks in the young-listing screen.** The spec says "US-listed common stocks", but the
   screen keeps about 25 non-stocks: commodity and crypto trusts (SIC 6221, e.g. IBIT, ETHA,
   GLDM), ETNs filed under the issuing bank's CIK (FNGU, SPYU, Bank of Montreal) and
   closed-end funds (PDI).
7. **No fundamentals, expected or out of scope (about 70 tickers).**
   - Foreign private issuers filing IFRS 20-F (ARM, NU, BIRK, HLN, KSPI, JBS).
   - Mortgage REITs and BDCs (AGNC, NLY, BXSL).
   - Listings too new to have a 10-Q (VYLR, TRAX, SKHY).
   - OZK, which files with the FDIC, not SEC.
   - Banks, which have no revenue line in the map.

## Audit fixes (2026-10-11, owner chose "data-layer fixes now")

Fixed in phase 1:

- **Findings 1 and 2 (c30530e).**
  - A concept can name fallback tags (partial lines).
    - In a filing without any of the concept's tags for a period, its fallback tags are summed.
    - A company whose fallback sum disagrees with its own total by more than 0.5%, on any
      period, gets no fallback rows at all.
  - Revenue fallbacks: SalesRevenueGoodsNet, SalesRevenueServicesNet.
  - Capex fallbacks:
    - other PP&E, other productive assets;
    - REIT capital improvements and development;
    - oil and gas development and property;
    - construction in process.
  - Not used:
    - PaymentsToAcquireRealEstate (acquisitions, not capex);
    - PaymentsForProceedsFromProductiveAssets (net of sale proceeds);
    - PaymentsToDevelopSoftware (often reported next to an extension-tag PP&E line).
  - A company's companyfacts rows are now replaced on refresh, not upserted.
  - A tag-map fingerprint (`facts_tag_map` in sync_state) makes the EDGAR step re-extract every
    stored company from cache after any tag-map or extraction-rule change. When the extraction
    code changes, bump `EXTRACTION_RULES` in `tags.py`.
- **Finding 3 (c0a8959).** 20 verified holding-company merges in `cik_merges.toml`.
  - Each predecessor's name and its revenue on shared periods were checked against SEC.
  - APO was rejected: only 7 of 16 periods agree.
  - SW, TKO, FTI, BKR, DD and CRH were left out: their accounting predecessor files no XBRL.
- **Finding 5 (4d72349).** Quarters are built from values as first reported.
  - "First reported" means the latest version filed within 366 days of the period end, so
    amendments are included.
  - A later comparative is used only when no earlier version exists.
  - `load_concept(all_versions=True)` returns every version; `load_quarters` wraps both.
  - Known remaining case: BAX Q3 2015, the Baxalta spin-off quarter. There the company's own
    year-to-date figures changed scope mid-year, so the derived quarter is negative.
- **Finding 6 (1bf77b7).**
  - `universe_on` excludes SIC 6221 (commodity and crypto trusts) next to blank checks (6770).
  - The young screen skips a listing that is not its CIK's first ticker in SEC's map when that
    first ticker is old (ETNs, further trust series, notes and new classes of old issuers).
    It deletes a stored row for such a listing and remembers the "secondary" verdict for 180
    days.

- **Follow-up after the second update (c0a8959..0b9d3b1).**
  - Fallback sums are checked only for filings without a total, against totals from other
    filings. A breakdown line next to the total in the same filing is neither used nor checked.
  - A company keeps its fallback rows when at least 80% of the checked sums agree; only the
    disagreeing periods are dropped.
  - The 80% bar is deliberate. Across all cached companies, 131 of 170 revenue and 47 of 117
    capex fallback users agree on 90% or more of checked periods. The middle band stays rejected,
    e.g. KO (75%) and GME (67%).
- **Result.** After `analyzer update` and `analyzer update --step edgar` (logs
  `update-2026-10-11b/c/d.log`), `analyzer audit` (`audit-2026-10-11d.log`) shows:

  | Measure | Before fixes | After fixes |
  |---|---|---|
  | Active tickers | 1,117 | 1,110 |
  | Flagged | 379 | 277 |
  | No FCF | 174 | 128 |
  | Check CIK merge | 140 | 71 |
  | No fundamentals | 87 | 85 |
  | Tickers with a recent FCF | 1,292 | 1,369 |

  - Also on that run: Wikipedia returned no 429 with the contact email (0 of 3 fetches failed),
    and 23 predecessor CIKs were backfilled. 893 young listings remain after 11 secondary ones
    were removed.
  - Live tests: 14 of 14 pass. Unit tests: 439 pass. Ruff is clean.

- **Review of the fixes (2026-10-11).** An independent review returned 1 critical and 10
  minor findings.
  - Fixed: 4 of them (the commit after 0195b73):
    - merged history keeps the predecessor's first report;
    - re-extraction survives an unreadable company;
    - the thresholds are part of the fingerprint;
    - the contact email is validated.
  - Not changed, on purpose:
    - *Fallback kept or dropped using later comparatives* (rated critical). This picks the
      meaning of a tag for a company; it does not move any value before its `filed` date.
      Phase 2 should know that this is hindsight-based cleaning, like a data vendor's tag
      mapping.
    - *Fallback sums with nothing to check against are used.* Deliberate: EOG, ADP, DLR and O
      have no standard total at all. They are flagged by no metric yet; phase 2 may mark them
      as unverified.
    - *Same-key instance rows are taken over by companyfacts.* Same value, and companyfacts
      supersedes; instance rows without a companyfacts twin are kept.
    - *A 404 leaves old rows.* Checked: no company is affected (0 of 4 uncached companies have
      rows).
    - *A 10-K/A recast within 366 days is differenced against the original 9 months.* Rare;
      revisit if the phase-2 research shows outliers.
    - *Secondary rule needs the primary judged "old"; the primary is SEC's first ticker;
      verdicts may be one screen stale.* A thinly traded primary keeps a real second class
      (BATRK). SEC's order matched the main listing in every sampled case, and expired
      verdicts are pruned on every save.

Deferred to phase 2:

- Finding 4: quarters missing from companyfacts. Covered by the existing prep item "build TTM
  values from annual figures".
- Finding 7:
  - IFRS foreign filers (ifrs-full namespace);
  - a bank revenue definition (net interest income plus noninterest income);
  - mortgage REITs and BDCs.
- Capex reported only under company extension tags: COP, PSX, NEE, URI, ABNB, DTE and about 30
  more.
- Revenue history before ASC 606 for companies whose fallback agreement is between 50% and 80%
  (KO, GME and about 12 more): review them one by one, or use annual figures there.
- Banks and other financials still show "check CIK merge" and "no fundamentals" (USB, WFC, COF,
  KEY, FITB…): this waits for the bank revenue definition.
- BAX Q3 2015: a negative derived quarter. The company's own year-to-date figures changed scope
  mid-year at the Baxalta spin-off.

## Next steps (resume here)

1. Resume the first update (planned for the morning of 2026-10-11):
   - Run `uv run analyzer update 2>&1 | tee data/logs/update-2026-10-11.log`.
   - It resumes:
     - stored prices are only topped up;
     - delisted misses are skipped for 30 days;
     - the HTTP cache is reused;
     - the universe step fetches only today's page and the 19 months that got 429.
   - Stop it with Ctrl-C in a terminal. A process started in the background with `&` ignores
     SIGINT; use `kill -TERM <python pid>`, which is safe because every write is a
     transaction.
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

## Decided 2026-10-11

- Wikipedia User-Agent contact: the owner chose a contact email. It is set as
  `WIKIPEDIA_CONTACT` in `.env` (not committed), and the Wikipedia client adds it to its
  User-Agent. No Wikipedia account is needed. The resumed update of 2026-10-11 started before
  this change, so the next update is the first to send it.

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
