# Task 27 report
Commit d1c2e75 "docs: phase-2 first in-sample run and status" (2 docs files only). No push/merge. Rules 2026.10.24-3.

## Backup
data/analyzer.db.bak-pre-phase2 (1732280320 bytes, equals original), plus -wal (0 B) and -shm (32768 B) copies.

## Update
- universe: `attempted=4 failed=0 (0.0%) latest=2026-10-24 tickers=2347 aliases=50 recycled=31 excluded=480` (exit 0, ~5 min)
- edgar: `attempted=1780 failed=1 (0.1%) latest=2026-10-23 companies=1777 changed=0 retried=0 insider_quarters=0 form4_xml=7327 reextracted=1799`. Exit 1 ("FAILED: edgar (provider_status not updated)") because `daily-index: FetchError: HTTP 403 for .../daily-index/2026/QTR4/master.20261012.idx`. Two instance-fallback warnings (invalid body index.json, 0001424182-26-000063, 0001486957-26-000042). Not retried. Facts re-extracted fine, so research proceeded. Daily-index scan is therefore incomplete; provider_status not advanced. Logs: data/phase2-update-universe.log, data/phase2-update-edgar.log.
- Prices were not refreshed (brief only runs universe+edgar); latest price date 2026-10-09.
- audit head (data/phase2-audit-head.txt): tickers 2358, active 1106, inactive 1252, with prices 1825, 8+ revenue quarters 1578, with FCF 1381, companyfacts behind 7, flagged 1106, reference ETFs without prices 0. Every flagged line shows "stale prices" (since prices end 2026-10-09).

## Research run (in-sample)
Report: data/research/20261024-125837-in_sample. Measured with /usr/bin/time -v: elapsed 8:16.49 (under 10 min target), peak RSS 2339192 kB.
- coverage share with prices: 2009 0/0, 2010 .693, 2011 .708, 2012 .737, 2013 .661, 2014 .692, 2015 .685, 2016 .725, 2017 .753, 2018 .785, 2019 .812, 2020 .837, 2021 .851 (rises toward 1 but 85% at best; not near 1.0)
- aliases {cik:50}; excluded acquired 145, other 108, recycled 31, unknown 227; identity_unverified 555; diluted_share_days 70857; annual_ttm_days 98283; fallback_days 100352
- skipped ticker-days: variant 23306, history 273707, required 200546, market_cap 8679
- groups: setup n=436 (median 63d xs SPY -1.15%, win 48.2%), dislocated n=3787 (-0.10%, 49.7%), dislocated_all n=4726 (-0.32%, 49.1%)
- tests: n PASS, median_xs_spy FAIL, win_rate FAIL, beats_dislocated FAIL, quintiles PASS; verdict FAIL (in-sample: tuning only)

## Spot checks (no browser; DB prices + facts)
- AAPL 2012-12-27: window 2012-09-27..12-27 high 24.36, low 17.90, close 18.395 -> drop ~24% (events drawdown 0.244). Facts: FY2012 capex 8.295B (10-K filed 2012-10-31), matches.
- BC 2018-10-26: high 69.82, close 50.63 (~27% drop; event 0.269). Facts: 10-Q filed 2018-08-01, capex Q2 2018 90.3M.
- UA 2021-12-17: high 22.94, close 17.71 (~22.8%; event 0.217). Facts: 10-Q filed 2021-11-04, capex 57.66M.
(Checked against DB, not an external chart or the 10-Q itself.)

## Concerns
- events.csv has 492 rows with group=setup vs summary groups.setup.n=436 (I did not investigate; likely rows for other periods/dedup or summary excludes missing outcomes). Worth a look.
- Coverage by year is 66-85%, 2009 empty; many members lack prices (acquired/unknown fates).
- EDGAR daily-index HTTP 403 (possible SEC user-agent/rate issue).
- No tests were run (no code changes). No code fixes made.
- Status section inserted right after the title line (not literally line 1).
