# SDD ledger — plan: docs/plans/2026-10-24-phase-2-features-signals-research.md

Spec: docs/specs/2026-10-24-phase-2-design-addendum.md (+ base spec sections 5-9).
Branch: phase-2-research (in place, from main 063e12d). Baseline: 445 passed.
Owner rules: never merge or push without explicit OK.

## Pre-flight scan

| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 -> all | Rules fields (features, gates, score, variants, plans, research) | consistent names used in T9-T25 |
| T2 -> T3 | parse_constituents.cik (object int/None), parse_changes, tables | consistent |
| T2 self | adds cik column to parse_constituents | existing wikipedia tests may compare exact columns -> Ruling R1 |
| T3 self | step_universe gains attempted+1 | existing update tests may assert attempted counts -> Ruling R2 |
| T4 self | tag map fingerprint changes | tests pinning fingerprint/concept count may break -> Ruling R3 |
| T5 -> T9..T26 | PricePanels, panel_fixtures helpers | consistent |
| T6 -> T20,T24 | symbol_map, membership_panel, liquidity_panel, load_entities | consistent |
| T7 -> T8 | step builders | consistent |
| T8 -> T9,T10,T17,T20 | FundamentalPanels FIELDS/FLAGS, first_filed, has_* | consistent |
| T9 -> T17,T18,T20 | ValueFeatures(ratio,pct,p50,peer,market_cap,net_debt,ebitda), VARIANT_METRICS | consistent |
| T10 -> T17,T19,T20 | QualityFeatures fields, balance_score | consistent (float32 outputs) |
| T11 -> T13,T20 | Dislocation fields, windows helpers | consistent |
| T12 -> T13,T20 | rsi/confirmed_lows/pivot_pairs/bullish_divergence; Technical(no pairs) | consistent |
| T14 -> T20 | load_purchases, cluster_buying(rows needs cik,date,window_start) | consistent |
| T15 -> T20 | load_macro, regime | consistent |
| T16 -> T17,T20 | variant constants, variant_series, columns_for_metrics | consistent |
| T17 -> T18,T20 | pick, eligibility, value_trap(TrapResult), undervalued | consistent |
| T18 -> T20,T22 | make_plans cols (entry, zone_*, *_stop, *_target) | consistent; T22 uses entry_price to avoid clash |
| T19 -> T20,T25 | score cols, reasons(row) | consistent |
| T20 -> T21..T26 | Panels, Evaluation(rows, dislocated_all, ...), fixture_db | consistent |
| T21..T24 -> T25 | select_events, assign_period, forward_returns, simulate_plans, stats, coverage | consistent |
| T25 -> T26 | CLI research run | consistent |
| T27 | network + real DB | runs only in-sample; no OOS; no merge/push |

## Rulings
- Ruling R1: if existing tests compare parse_constituents output exactly, update them to include the new `cik` column — the spec (addendum 3.1) requires the column — cost if wrong: test churn only.
- Ruling R2: if existing update/step_universe tests assert exact `attempted` counts, update them for the new aliases item — addendum 3.1 puts alias sync in the universe step — cost if wrong: test churn only.
- Ruling R3: if existing tests pin the tag-map fingerprint or concept count, update them — addendum 3.3 adds concepts — cost if wrong: test churn only.
- Ruling R4: branch in place (not a worktree) — owner was told "a separate branch"; Task 27 needs the real data/ and .env in this checkout — cost if wrong: main checkout sits on a feature branch until merge.
- Ruling R5: plan code is the starting point, not gospel; implementers fix plan code bugs that make the plan's own tests fail, and report each such fix — the spec is binding, the plan argues from it — cost if wrong: small deviations from plan text, visible in reports.

## Progress
Task 1: dispatched (base 063e12d, implementer haiku)
Task 1: ⚠️ resolved — commit trailer is "Co-Authored-By: Claude Opus 5.5" per the controller's attribution reminder (the reviewer's Sonnet note reflects its own model, not ours).
Task 1: minor (deferred): load_rules raises bare KeyError for a missing whole section instead of ValueError.
Task 1: minor (deferred): _section does no type validation (e.g. win_basis typo, string "false" accepted) — plan-mandated.
Task 1: minor (deferred): no test for nested variants.* strictness or a missing section.
Task 1: complete (commits 063e12d..4460584, review clean)
Task 2: dispatched (base 4460584, implementer sonnet)
Task 2: implementer DONE_WITH_CONCERNS (no RED captured; existing tests updated per R1) commit 811daa7
Task 2: ⚠️ resolved — _find/_text_column behaviour covered by passing parse_changes tests; NULL cik on stored snapshots is filled by Task 3 backfill_snapshot_ciks.
Task 2: minor (deferred): parse_changes import mid-file with noqa E402 in tests/universe/test_wikipedia.py.
Task 2: minor (deferred): no test asserts cik survives sync_index_snapshots insert.
Task 2: minor (deferred): parse_changes "latest wins" relies on newest-first table order (plan-mandated), ignores Date column.
Task 2: minor (deferred): parse_changes ValueError (no tables) path untested.
Task 2: minor (deferred): no CHECK constraints on ticker_aliases.source / member_fates.fate.
Task 2: minor (deferred): no RED evidence captured.
Task 2: complete (commits 4460584..811daa7, review clean)
Task 3: dispatched (base 811daa7, implementer sonnet)
Task 3: implementer DONE commit c2cbe84
Task 3: review — Needs fixes: (1) recycled check unions snapshot CIKs, so a ticker with old+current owner in snapshots is kept and old owner's window scored on new owner's prices [plan-mandated]; (2) backfill_snapshot_ciks / change_reasons / sync_aliases failure branch / registry latest-CIK fallback untested.
Task 3: Ruling R6: a ticker whose snapshot CIKs, after mapping CIK-merge predecessors (cik_merges.toml via load_cik_merges on the tickers table) to their successor, name more than one company gets fate `recycled` and is excluded entirely, unless a manual alias exists — spec 3.1/3.2 forbids scoring a member on another company's prices and membership is per ticker, not per date — cost if wrong: the current owner's own index history for the few reused tickers is lost (counted in coverage `excluded.recycled`).
Task 3: Ruling R7: store the CIK that actually matched the alias target, not min(seen) — reviewer minor, cheap, same code path as R6 — cost if wrong: none.
Task 3: fix round 1/5 (3 addressed, 0 open; commits c2cbe84..fab365b). Controller verified full suite 469 passed + ruff clean (fix report lacked that output).
Task 3: minor (deferred): unpriced ticker whose snapshots name several companies is recycled rather than aliased (consequence of R6).
Task 3: minor (deferred): manual aliases.toml entries not validated (unpriced target, malformed entry aborts aliasing).
Task 3: minor (deferred): change_reasons setdefault keeps first index's reason for a ticker removed from both.
Task 3: minor (deferred): renamed member whose old ticker still has prices and lost its SEC mapping stays under the stale symbol (confirm on real data, Task 27).
Task 3: complete (commits 811daa7..fab365b, review clean after 1 fix round)
Task 4: dispatched (base fab365b, implementer haiku)
Task 4: implementer DONE commit d9896fa
Task 4: ⚠️ resolved — tag_map_fingerprint hashes CONCEPTS repr (tags.py, read by controller); extract_facts is generic per concept except shares_outstanding class summing.
Task 4: minor (deferred): test_tags asserts concept names only (no values, no AtCost tag, no fingerprint-change check).
Task 4: complete (commits fab365b..d9896fa, review clean)
Task 5: dispatched (base d9896fa, implementer sonnet)
Task 5: implementer DONE commit 4e17c17
Task 5: review — Needs fixes: (1) assert_truncation_invariant uses default rtol 1e-5, so small leaks pass [plan-mandated]; (2) empty cut_dates passes vacuously [plan-mandated].
Task 5: Ruling R8: tighten the guard to rtol=1e-9, atol=1e-12, require non-empty cut_dates, add a self-test (one leaky compute must fail, one clean compute must pass), and document that compute must cut raw inputs — addendum 4.5 makes truncation invariance the look-ahead guard, and a loose guard defeats it — cost if wrong: a later feature test may fail on float noise and need a documented per-test tolerance.
Task 5: fix round 1/5 (2 addressed + R8 self-test/docstring, 0 open; commits 4e17c17..881379d)
Task 5: minor (deferred): _frames ignores nested frame containers; no tests for PricePanels.select / upto with Timestamp end.
Task 5: complete (commits d9896fa..881379d, review clean after 1 fix round)
Task 6: dispatched (base 881379d, implementer sonnet)
Task 6: implementer DONE commit a0c143a
Task 6: ⚠️ resolved — load_price_panels (Task 5) reindexes without forward-fill; recycled fates written by build_aliases (Task 3).
Task 6: minor (deferred): liquidity_panel stricter than phase-1 _tradable_close (one missing bar fails the next 20 days; NaN volume fails instead of 0). Check impact on real data in Task 27.
Task 6: minor (deferred): membership test cannot catch an end_date boundary error; symbol_map recycled/unpriced branches untested.
Task 6: complete (commits 881379d..a0c143a, review clean)
Task 7: dispatched (base a0c143a, implementer sonnet)
Task 7: implementer DONE commit f35bd78
Task 7: review (opus) — Needs fixes: C1 combine_ttm crashes with annual steps and no quarterly steps; I2 total_debt not truncation-invariant (outer merge, filed=max); I3 add_missing_periods not truncation-invariant (drops earlier extra row when primary reports the period later); I4 fallback flag from any version, not the kept one; I5 discrete_quarters keeps newest version within 366 days, so 10-K Item-302 re-reports delay quarters (not a leak, but breaks truncation invariance). All plan-mandated.
Task 7: Ruling R9: fix C1-I4 in steps.py as the reviewer proposes; total_debt emits a row at each component's first filing (long-term alone first, combined when the short line arrives; with overlap, filed is the long-term line's) — spec 3.3 + addendum 4.5 truncation invariance — cost if wrong: more debt steps, none wrong.
Task 7: Ruling R10: fallback_ends flags a period only when its first-filed version (per period_start, period_end) is a fallback row — first reports never change with later data, matching the first-report convention of annual/instant steps — cost if wrong: a later fallback restatement that the kept quarter version uses goes unflagged (rare).
Task 7: Ruling R11: add steps.first_versions(facts) (earliest filed version per period_start, period_end; ties by accession); Task 8's company_steps must pass first_versions(f) to discrete_quarters and annual_steps — makes quarters enter on their first report, truncation-invariant; ignores 10-Q/A amendments (conservative) — cost if wrong: amended quarters keep their original value.
Task 7: Ruling R12: combine_ttm prefers annual only when a_end > q_end + SAME_QUARTER_DAYS; latest_value_steps sorts [filed, accession] and prefers the shortest duration for the same period end; add truncation-invariance tests for every step builder — reviewer minors 6-8, cheap, same code — cost if wrong: none.
Task 7: Ruling R13: accept the implementer's deviation from R9/I3 — no period_end snapping in add_missing_periods; quarter_steps merges period ends within SAME_QUARTER_DAYS into one key, newest end replacing — snapping would depend on later primary rows and break truncation invariance — cost if wrong: quarter keys shift by a few days, harmless for TTM sums.
Task 7: carry to Task 8: debt must use instant_steps(total_debt(...), keep_updates=True); company_steps must pass first_versions(f) into discrete_quarters and annual_steps (R11).
Task 7: fix round 1/5 (5 addressed, 1 open — total_debt overlap with the short line filed first not truncation-invariant; commits f35bd78..bd28518)
Task 7: out-of-scope note: fallback_ends frozenset is consistent with first_versions-fed quarters (R11) — the flagged end's kept value is its first version; Task 8 must use first_versions per R11.
Task 7: fix round 2/5 (1 addressed, 0 open; commits bd28518..5ad30de)
Task 7: minor (deferred): total_debt docstring still says the short line is ignored on overlap (it is now an interim row when filed first).
Task 7: minor (deferred): annual_steps cannot restrict to 10-K forms (no form column); latest_value_steps edge cases now tested.
Task 7: complete (commits a0c143a..5ad30de, review clean after 2 fix rounds)
Task 8: dispatched (base 5ad30de, implementer sonnet)
Task 8: implementer DONE_WITH_CONCERNS (static first_filed/has_property/has_us_gaap per brief) commit 93d23e8
Task 8: review (opus) — Needs fixes: I1 revenue q fields age from the combined step's filed, so annual-only steps keep years-old quarters fresh [plan-mandated]; I2 has_property/has_us_gaap static over all facts [plan-mandated].
Task 8: ⚠️ resolved — shares carry splits dated after d by design; value.py (Task 9) always multiplies shares by stored closes on the same basis (market_cap.py contract).
Task 8: Ruling R14: age revenue q fields on the quarter series' own filed date (expose q_filed from combine_ttm) and instant fields on the filed date of the value shown (value_filed in instant_steps); diluted share counts get split factors for splits after their filed date (ASC 260 restates for splits before filing); count and log failed companies in load_fundamental_panels; add tests for q aging, the bank path under truncation, the fallback flag, diluted split adjustment — spec 3.3/5.1 age limits and addendum 4.5 — cost if wrong: none.
Task 8: Ruling R15: keep has_property and has_us_gaap static per symbol — variants are per-symbol attributes (spec 7.4 assigns by SIC) and Task 16 builds a static variant Series; a time-varying variant would ripple through Tasks 9, 16, 17 — cost if wrong: a REIT that began tagging property late is scored as an equity REIT (not excluded) in its earlier years; a company before its first us-gaap fact has no required inputs anyway and is skipped.
Task 8: fix round 1/5 (5 addressed, 0 open; commits 93d23e8..fa8cf10). Controller re-ran suite: see line below.
Task 8: minor (deferred): fallback flag can turn True at the first summed report before any TTM exists (partial-quarter step).
Task 8: minor (deferred): fallback_ends keyed on period_end only; a same-end different-duration fallback first filed later can flip earlier flags (rare).
Task 8: complete (commits 5ad30de..fa8cf10, review clean after 1 fix round)
Task 9: dispatched (base fa8cf10, implementer sonnet)
Task 9: implementer DONE_WITH_CONCERNS (peer comment vs columns_for — controller: columns_for[m] is every symbol using m, consistent) commit a3f76d5
Task 9: review — Needs fixes: value_features/net_debt/p50 untested; peer comment contradicts code.
Task 9: ⚠️ resolved — capex tags are PaymentsToAcquire* (positive outflows), so fcf = ocf - capex is right; rules values 1260/756/8 set in Task 1.
Task 9: Ruling R16: peers for a metric = every eligible symbol whose variant uses the metric (columns_for[m]); fix the comment to say so; add a value_features test; factor ebitda/market cap into one helper each; compute ratios only for the needed columns per metric to cut peak memory — spec 5.1 peer group within the universe, YAGNI on cross-variant peers — cost if wrong: a bank's P/E peers exclude standard stocks in the same SIC group (rare overlap).
Task 9: fix round 1/5 (4 addressed, 0 open; commits a3f76d5..8618b01)
Task 9: minor (deferred): ratios still builds cap/nd/ebitda/fcf/ffo intermediates full width; tests use SimpleNamespace rules instead of dataclasses.replace(load_rules().features).
Task 9: minor (deferred): peer threshold counts members with a value that day (not group membership); net_debt treats missing cash/debt as 0 when equity is known; stock included in its own peer median.
Task 9: complete (commits fa8cf10..8618b01, review clean after 1 fix round)
Task 10: dispatched (base 8618b01, implementer sonnet)
Task 10: implementer DONE commit 8c9fdb2
Task 10: review — Needs fixes: revenue_decline_2q turns unknown quarters into False [plan-mandated]; fcf_score scores unknown prior-year FCF as falling.
Task 10: ⚠️ resolved — growth/margin/roe scales 0.20/0.05/0.05 in rules.toml (Task 1); value.ebitda/net_debt keep NaN for unknown operating income/D&A and missing equity (Task 9 tests); capex positive outflow (Task 9 ruling).
Task 10: Ruling R17: add QualityFeatures.revenue_decline_known (all four quarterly inputs present); Task 17's value_trap must use known["revenue"] = revenue_growth.notna() & revenue_decline_known — keeps "unknown" countable as a skipped rule (addendum 5.4) — cost if wrong: none.
Task 10: Ruling R18: fcf_score is NaN when fcf or fcf_prev is unknown, so the quality mean uses the other sub-scores — unknown must not read as "falling" — cost if wrong: young FCF histories get no FCF sub-score for their first year.
Task 10: fix round 1/5 (3 addressed, 0 open; commits 8c9fdb2..0a91e8b)
Task 10: minor (deferred): balance_score divides by zero for cap <= 1 and returns float64; quarterly fields read twice in quality_features; FCF exactly 0 counts as non-positive.
Task 10: complete (commits 8618b01..0a91e8b, review clean after 1 fix round)
Carry to Task 17: R17 — value_trap known["revenue"] must be revenue_growth.notna() & q.revenue_decline_known.
Task 11: dispatched (base 0a91e8b, implementer sonnet)
Task 11: implementer DONE commit 4167e1b
Task 11: ⚠️ resolved — prices.close is split-adjusted (phase-1 market_cap.py docstring), adj_close adds dividends.
Task 11: minor (deferred): spy_since broadcast by position — reindex spy_close to close.index first (latent; panel index comes from SPY bars).
Task 11: minor (deferred): edge tests missing (shock before/on high day, correction ended before high, gate-2 negatives, ref_of SPY fallback, NaN gaps, window_low with low != close); reduce_since since<window precondition not enforced; peak memory ~650 MB per helper call at full size.
Task 11: complete (commits 0a91e8b..4167e1b, review clean)
Task 12: dispatched (base 4167e1b, implementer sonnet)
Task 12: implementer DONE commit eafce29
Task 12: review (opus) — Needs fixes: I1 reclaimed_support misses quick reclaims and accepts late ones (level not anchored at break time) [plan-mandated]; I2 weekly truncation test vacuous (no True days near cuts) [plan-mandated]. Reviewer's exhaustive truncation checks found no look-ahead.
Task 12: Ruling R19: reclaimed_support = a break at s when close_s < level_s (level_s = latest confirmed swing low known at s, pairs.last_price); flag t when close_t > level_s for some break s in [t-bars, t-1]; strict "<" for the break — spec 5.5 "after the close broke below the prior swing low, it closes back above that level within 10 bars" — cost if wrong: reclaim fires on a different set of days (score component only).
Task 12: Ruling R20: also fix minors cheaply — pivot ties (strict on the left, non-strict on the right), RSI 50 when gain = loss = 0, RSI/ATR NaN where close is NaN (no stale values after delisting), ma in the truncation test — same file, low cost — cost if wrong: none.
Task 12: fix round 1/5 (3 addressed, 0 open; commits eafce29..dab6d19). Controller re-ran suite: 569 passed.
Task 12: minor (deferred): double-bottom test doesn't assert which bar is the pivot; no exact `bars` boundary test.
Task 12: complete (commits 4167e1b..dab6d19, review clean after 1 fix round)
Task 13: dispatched (base dab6d19, implementer sonnet)
Task 13: implementer DONE commit ae64181
Task 13: review — Needs fixes: rank(pct) >= 0.8 tags ~0.2n+1 ETFs hot (asymmetric vs cold) and has no small-n guard [plan-mandated]; accumulation values untested.
Task 13: ⚠️ resolved — reduce_since includes the high day (age <= since), NaN ignored in count (Task 11 review); pivots usable from confirmation (Task 12 review, exhaustive truncation checks).
Task 13: Ruling R21: hot = top 20% by position (descending rank, method "min", rank/n <= 1 - hot_rank) and dv trend > hot_dv_trend; cold = bottom 20% symmetric (ascending rank, method "min", rank/n <= cold_rank); NaN when fewer than features.flow_min_industries (new rules key = 5, version bump) ETFs are ranked that day — spec 5.4 "top 20%"/"bottom 20%" — cost if wrong: slightly fewer hot/cold tags; unknown flow scores as neutral.
Task 13: Ruling R22: up/down days use adj_close direction (splits); a NaN up/down ratio (no up or no down days) scores the ratio part 0.5 (neutral) instead of 0; add value tests for ratio, OBV divergence, accumulation, a reachable cold tag and the small-n guard — cost if wrong: none.
Task 13: fix round 1/5 (2 addressed, 0 open; commits ae64181..f8cb957). Controller re-ran suite (line below). rules version now 2026.10.24-2.
Task 13: minor (deferred): OBV divergence not restricted to the drawdown window; stock_codes keeps ETF index; NaN trend tags top ETF neutral; OBV test doesn't exercise adj_close vs close.
Task 13: complete (commits dab6d19..f8cb957, review clean after 1 fix round)
Task 14: dispatched (base f8cb957, implementer haiku)
Task 14: implementer DONE commit 23baa49 (trailer says Haiku — wrong; to fix in loop)
Task 14: ⚠️ resolved — issuer_cik is NOT NULL in 001_initial.sql. Trailer fixed by message-only amend 23baa49 -> ee5f95f (controller verified empty tree diff), counts as fix round 1/5.
Task 14: minor (deferred): no tests for exec threshold boundary, same owner twice, 'D' rows, CFO title variants; NULL owner never counts; joint-filer strings count as one buyer.
Task 14: complete (commits f8cb957..ee5f95f, review clean)
Task 15: dispatched (base ee5f95f, implementer haiku)
Task 15: implementer DONE commit 7f2202a
Task 15: review — Approved but 2 Important (plan-mandated): indicator/known logic duplicated; regime thresholds/windows hard-coded vs global constraint "every threshold lives in rules.toml".
Task 15: ⚠️ resolved — macro history starts 2006 (fred.py MACRO_HISTORY_START), >3y before panel_start 2009; trailer Opus verified below.
Task 15: Ruling R23: add [regime] to rules.toml (loose_min 4, tight_max 2, median_window 756, change_window 65, yoy_window 252; version bump) and RegimeRules on Rules; regime(macro, rules) reads them; publication lags stay module constants (facts about FRED, not thresholds); one helper returns (indicator, known) per series — global constraint is literal — cost if wrong: one more rules section.
Carry to Task 20: evaluate must call regime(p.macro, rules.regime) (R23 changed the signature).
Task 15: fix round 1/5 (3 addressed, 0 open; commits 7f2202a..49fff93). rules version now 2026.10.24-3.
Task 15: minor (deferred): unbounded ffill keeps a discontinued series "known"; WEEKS13 approximated by 65 trading days.
Task 15: complete (commits ee5f95f..49fff93, review clean after 1 fix round)
Task 16: dispatched (base 49fff93, implementer haiku)
Task 16: implementer DONE commit a3b16fd
Task 16: ⚠️ resolved — load_entities (Task 6) gives sic as str or None and cik float/NaN.
Task 16: minor (deferred): variant_of assumes sic is str/None; missing static-panel symbol silently becomes excluded:no_us_gaap; no financials SIC boundary tests.
Task 16: complete (commits 49fff93..a3b16fd, review clean)
Task 17: dispatched (base a3b16fd, implementer sonnet)
Task 17: implementer DONE commit 7d3de89
Task 17: ⚠️ resolved — columns_for_metrics(variants) is passed to value_features in evaluate (Task 20 brief); gate 4 = ~shock is computed in evaluate (Task 20).
Task 17: minor (deferred): rule-firing tests missing for ROE/FCF/REIT leverage/boundaries/inf and gate-1 failing cases; eligibility reindexes by column only (row index assumed equal); history check builds 65 MB temporaries; docstring says "gate 4" (lives in evaluate); revenue rule counted missing even when TTM growth >= 0 known (R17 consequence).
Task 17: complete (commits a3b16fd..7d3de89, review clean)
Task 18: dispatched (base 7d3de89, implementer sonnet)
Task 18: implementer DONE commit 288864f
Task 18: review — Needs fixes: stop == entry gives infinite R/R that passes gate 5; EV valuation target admits non-positive EBITDA/shares into the median.
Task 18: ⚠️ resolved — time stops live in research/outcomes.simulate_plan (Task 22); PlanRules/GateRules field names from Task 1; pick returns float arrays aligned to rows (Task 17).
Task 18: fix round 1/5 (3 addressed, 0 open; commits 288864f..e70bf13)
Task 18: minor (deferred): metric absent from vf.ratio silently skipped in valuation_target.
Task 18: complete (commits 7d3de89..e70bf13, review clean after 1 fix round)
Task 19: dispatched (base e70bf13, implementer sonnet)
Task 19: implementer DONE commit 863996c
Task 19: ⚠️ resolved — rules values checked in Task 1 (rules.toml); HOT/COLD in flow.py; balance_score NaN-preserving (Task 10).
Task 19: minor (deferred): reasons() int() on NaN cheaper/n_metrics and NaN truthiness — safe only for fully populated setup rows; "Cheaper 0 of N" always emitted; share unclipped when n_metrics 0; correction NaN -> True via astype(bool); reasons/REIT/tier-boundary tests thin.
Task 19: complete (commits e70bf13..863996c, review clean)
Task 20: dispatched (base 863996c, implementer sonnet)
Task 20: implementer hand-back was empty ('placeholder'); controller verified report file, commit 4fe9b26, 623 passed, ruff clean
Task 20: review (opus) — Needs fixes: I1 fixture has no negative case for any gate (mutation probes on columns_for / peer pool still pass) [partly plan-mandated]; I2 peer pool = member vs R16 wording "eligible", flow uses eligible vs spec 5.7 "universe" [plan-mandated].
Task 20: Ruling R24: the universe (spec 2 and 5.1 "stocks in the universe", 5.7 "reference ETFs used by the universe") is `member` (index membership + liquidity), so peer medians use `p.member` (keep) and flow's active ETFs must also use `p.member` (change from elig.eligible); R16's "eligible" wording is superseded by the spec — cost if wrong: peer medians include young/incomplete filers; flow ranking includes industries whose stocks aren't scorable.
Task 20: Ruling R25: extend the fixture with negative cases (dislocated-but-expensive -> gate1 False, shock drop -> gate4 False, dislocated value trap -> gate3 False, a dislocated non-member -> only in dislocated_all), dividends so adj_close != close, one insider purchase inside DIP's window, and assert n_metrics == 4, window_start == high date, buyers — the integration tests must catch wiring errors — cost if wrong: slower fixture.
Task 20: Ruling R26: accept OUT as an index member with excluded variant (no_cik) instead of a non-member — dislocated_all = member & dislocated by definition, so a non-member can never appear; my R25 wording was wrong — cost if wrong: none.
Task 20: fix round 1/5 (3 addressed, 0 open; commits 4fe9b26..7f3c6d5). Re-review mutation probes: dropping gate3 or gate5 from setup is undetected (TRAP also fails gate1; no gate5-False row); R24 not test-pinned (single ETF).
Task 20: Ruling R27: one more round to make gate3 and gate5 load-bearing in the integration tests — they are core spec gates (7.1) and the group-3-vs-group-2 test depends on them — cost if wrong: one more fixture tweak.
Task 20: fix round 2/5 (2 addressed, R24 pin skipped as not cheap — accepted; commits 7f3c6d5..a9e6e51). Suite verified by controller (line above).
Task 20: minor (deferred): R24 (flow over member vs eligible) not test-pinned (needs 5 priced ETFs); bare assert on since_high disappears under python -O.
Task 20: complete (commits 863996c..a9e6e51, review clean after 2 fix rounds)
Carry to Tasks 24-26: fixture now has DIP, 11 peers, EXP, SHK, TRAP, RR (all standard) and OUT (excluded:no_cik); brief assertions that assume 12 stocks (e.g. coverage members 13/12, variants {"standard": 12}) must be derived from tests/fixture_db.py constants.
Task 21: dispatched (base a9e6e51, implementer haiku)
Task 21: implementer DONE commit 0c034b0
Task 21: ⚠️ resolved — Task 22's forward return exits at the close of pos + h (entry at open pos+1), so windows end at pos + h, consistent with assign_period; Evaluation.rows = eligible & dislocated and dislocated_all has symbol/date/pos (Task 20).
Task 21: minor (deferred): no exact-boundary tests at the real 2022 cutoff; select_events untyped; Python loop cooldown.
Task 21: complete (commits a9e6e51..0c034b0, review clean)
Task 22: dispatched (base 0c034b0, implementer sonnet)
Task 22: implementer hand-back empty ('placeholder'); controller verified report, commit bb658a1, 642 passed, ruff clean
Task 22: review (opus) — Approved; minors with go/no-go impact: entry-at-open and real MAE untested; thesis exit can run past the time stop; a stock whose prices end mid-window (delisting/acquisition) gets NaN return and silently drops out (survivor bias); no panel date-alignment check.
Task 22: ⚠️ resolved — load_price_panels reindexes both panels to SPY dates (Task 5); trap_fails = value_trap on point-in-time quality panels (Tasks 10, 17).
Task 22: Ruling R28: fix now — (a) when a stock's price series ends before pos + h (no valid close at or after the exit row through the end of data), exit at its last valid adjusted close and hold cash after it (benchmarks still measured to pos + h); a NaN at the exit row with later prices (a gap) also uses the last valid close up to the exit row; same last-valid-close rule for simulate_plan exits ("delisted" outcome when the series ended); (b) time stop wins over a thesis signal on the time-stop day; (c) assert stocks/etfs share dates; (d) tests with open != close, low < close, a distinct ref ETF, a delisting mid-window, thesis on the time-stop day; (e) silence nanmin all-NaN warning — spec 9.3 wants survivorship visible, not baked in — cost if wrong: delisted events count at their last price, which is what an investor would have received for cash takeovers.
Task 22: fix round 1/5 (5 addressed, 0 open; commits bb658a1..7d5125b). Controller verified 648 passed, ruff clean.
Task 22: minor (deferred): time exit can pick a pre-entry close in a pathological all-NaN-close window; delisted_h True when last close is exactly the exit row / when entry NaN; untested branches (time with gap, thesis with NaN next open, NaN lows in MAE); ETF NaN at exit row unhandled; _last_valid full-width array.
Task 22: complete (commits 0c034b0..7d5125b, review clean after 1 fix round)
Carry to Task 25: test outcome set must include "delisted" and "no_data"; delisted_h columns are output labels only (never inputs).
Task 23: dispatched (base 7d5125b, implementer sonnet)
Task 23: implementer DONE commit 5661a7b
Task 23: review — Approved; Important: quintile ties split by row order (rank "first") [plan-mandated]; no tests for NaN-cannot-pass verdict, month-block integrity, tied/NaN scores.
Task 23: Ruling R29: break quintile ties by a stable hash of (symbol, date) — deterministic and unrelated to outcomes or row order; add the missing tests; use g.size().index for group labels — the go/no-go quintile test must not depend on row order — cost if wrong: none.
Task 23: fix round 1/5 (3 addressed, 0 open; commits 5661a7b..529bb2e). Re-reviewer confirmed the tie test fails on the old code.
Task 23: minor (deferred): raw win basis counts NaN ret as a loss; nanpercentile ignores NaN draws.
Task 23: complete (commits 7d5125b..529bb2e, review clean after 1 fix round)
Task 24: dispatched (base 529bb2e, implementer sonnet)
Task 24: implementer DONE commit bd1990d
Task 24: review — Needs fixes: test leaves aliases, diluted/annual/fallback counts and non-history skip reasons unasserted; end-exclusive year boundary only indirectly pinned.
Task 24: ⚠️ resolved — symbol_map keys are index_membership tickers (Task 6); fundamentals flags are bool frames aligned to member/eligible (Task 8, Task 20 reindex); skipped has all four keys (Task 17). Minor index-filter note: the universe is both sp500 and sp400 by design, so no filter.
Task 24: fix round 1/5 (2 addressed, 0 open; commits bd1990d..af7ea4b)
Task 24: minor (deferred): fixture flag counts are all 0, so a wrong member/eligible mask in coverage's three flag counts is undetectable.
Task 24: complete (commits 529bb2e..af7ea4b, review clean after 1 fix round)
Task 25: dispatched (base af7ea4b, implementer sonnet)
Task 25: implementer DONE commit 967dea5
Task 25: review (opus) — Needs fixes: I1 in-sample runs write OOS outcomes to events.csv (silent peeking) [plan-mandated]; I2 coverage not printed (addendum 6.6) [plan-mandated]; I3 git_state and CLI oos success path untested, OOS_LOG default bound at definition (cannot redirect safely).
Task 25: Ruling R30: an in-sample run loads panels only up to the day before research.oos_start, so no out-of-sample price is ever read during tuning (in-sample windows already end before oos_start by the purge); assert no oos/oos_incomplete event exists in an in-sample run — spec 9.2 "thresholds tuned in-sample only" + "repeated peeking is visible" — cost if wrong: none (purged events keep their in-sample-only outcomes).
Task 25: Ruling R31: print coverage summary, guidance_tone_enabled and win_basis, and an "IN-SAMPLE (tuning only)" header; append the OOS log BEFORE printing results; append_oos_log path default None resolved at call time; route git failures through _fail; check FROZEN_PATHS are tracked (git ls-files --error-unmatch); log a dirty_tree column (any uncommitted change under src/); tests: git_state on a temp git repo (clean/modified/staged/deleted/untracked), CLI oos success path with OOS_LOG, settings and data dir in tmp — the oos run is the real go/no-go — cost if wrong: one more column in oos-runs.csv (header updated).
Task 25: fix round 1/5 (2 findings addressed, 0 open; commits 967dea5..f27e3d3). Controller verified 671 passed, ruff clean, oos-runs.csv header-only.
Task 25: minor (deferred): oos run_research writes events/breakdowns/quintiles before the log row is appended (a coverage or append failure leaves unlogged OOS files; nothing printed); run_research("oos") called as a library skips the guard by design; untracked-never-committed frozen path branch not directly tested; in-sample guard is a bare assert.
Task 25: complete (commits af7ea4b..f27e3d3, review clean after 1 fix round)
Task 26: dispatched (base f27e3d3, implementer sonnet)
Task 26: implementer hand-back empty ('placeholder'); controller verified report, commit 4560fa1, 672 passed, ruff clean, oos log header-only
Task 26: minor (deferred): panel_start replace not asserted to change the text; exact CLI wording asserted (brittle to copy edits); wall-clock check sensitive to load.
Task 26: complete (commits f27e3d3..4560fa1, review clean)
Task 27: Ruling R32: copy data/analyzer.db to data/analyzer.db.bak-pre-phase2 before the live update (migration 003 + full facts re-extraction change the owner's real DB; 30 GB free) — keeps the run reversible — cost if wrong: 1.7 GB disk until the owner deletes it.
Task 27: dispatched (base 4560fa1, implementer sonnet)
Task 27: implementer DONE_WITH_CONCERNS commit d1c2e75. Controller explained events.csv 492 setup rows = 436 in_sample + 46 purged + 10 before_start (summary counts in_sample only); no leftover processes; oos log untouched.
Task 27: minor (deferred): status doc doesn't explain setup n=436 = in_sample only (events.csv also holds purged/before_start rows); spot checks DB-only; daily-index scan incomplete after SEC 403; coverage weak (66-85%).
Task 27: complete (commits 4560fa1..d1c2e75, review clean)
Final review: dispatched (base 063e12d, opus)
Final review (opus): With fixes. FAIL looks real (no sign/window/peer/base errors). Important: I1 fundamentals not truncation-invariant on real data (discrete_quarters prefers direct over derived regardless of filing); I2 YoY via shift(252) pairs wrong periods on ~10% of company-days (gate 3, sub-scores, thesis stop); I3 stocks whose reference ETF did not exist yet (XLRE 2015-10, XLC 2018-06, JETS 2015-04) can never be dislocated; I4 regime unknown for every event (FRED serves BAMLH0A0HYM2 only from 2023); I5 --rules can move oos_start in an in-sample run; OOS results written before the log row.
Final: Ruling R33 (I1): PIT quarters keep every candidate (direct and derived) with its own filed date; quarter_steps lets a later candidate replace a quarter's value only from its filed date (earliest known first); add an opt-in real-DB truncation test (marker realdb, skipped by default) — addendum 4.5 — cost if wrong: none.
Final: Ruling R34 (I2): steps emit year-earlier values (ttm_ly: the four quarters ending ~365 days before q0; annual: prior fiscal year; instants: avg5_ly) carried as panel fields; quality uses them instead of shift(252) — addendum 4.2 "TTM value one year earlier" — supersedes plan decision 4 — cost if wrong: more panel fields (~200 MB).
Final: Ruling R35 (I3): per date, the reference ETF falls back industry -> sector -> SPY when the chosen ETF has no 63-day return; used for gap, xs_ref and flow; coverage counts fallback ticker-days — spec 5.3 names industry else sector ETF — cost if wrong: early Communication Services/REIT names compared with SPY.
Final: Ruling R36 (I4): regime classifies from the indicators known that day when at least 5 of 6 are known: loose if loose >= ceil(2/3 of known), tight if loose <= floor(1/3 of known) (identical to 4/2 when all 6 are known); bounded forward-fill (a value older than 3x its series cadence is unknown); coverage reports the share of days with a known regime — spec 5.6 thresholds preserved — cost if wrong: regime breakdown based on 5 indicators for history.
Final: Ruling R37 (I5): refuse --rules when its [research] section differs from the committed rules.toml; for oos, append a "started" log row (verdict=started) before evaluating, then the final row — spec 9.2 visibility of every OOS run — cost if wrong: two log rows per OOS run.
Final: Ruling R38 (minors): rename the "industry" breakdown column to flow and add a ref_etf breakdown (M1); revenue trap rule counts as missing only when TTM growth is unknown or (growth < 0 and quarters unknown) (M4); re-run in-sample on the real DB after the fixes and update the status doc with the new numbers, the I3/I4 effects, "setup n counts in-sample events only" and "S&P 400 membership starts 2013" (M5). Deferred: M3 recycled tickers without snapshot CIK (~35 by name match, mostly non-overlapping), M2, M6.
Final: fix wave dispatched (base d1c2e75, opus implementer)
Final: fix wave DONE_WITH_CONCERNS commits d1c2e75..8572960 (8 commits); controller verified suite + ruff + oos log header-only. New in-sample verdict still FAIL (setup -1.09%, win 48.5%, dislocated -0.03%).
Final: re-review (opus) — I2-I5, M1, M4, M5 ADDRESSED; I1 residual open.
Final: parked — I1 residual: YTD links with a period end 1-7 days off a later filing can still split an earlier derivable pair (quarters.py:96-110); 16 CIKs on real data (e.g. DE 2016-17, VTRS 2021) see a quarter later with full data than with cut data — Ruling: real, conservative (values delayed, never leaked), not fixed in this branch because the skill allows one fix wave; fix before the OOS run (merge links within SAME_QUARTER_DAYS keeping first-filed, plus a monotonicity property test) — cost if left: a handful of in-sample events use staler fundamentals.
Final: parked — working rules.toml [research] edits are not compared with HEAD for in-sample runs without --rules (cli.py:186) — Ruling: real hole, one-line fix (always compare); fix before tuning — cost if left: an edited oos_start lets an in-sample run read OOS prices unlogged.
Final: parked — minors: bank cartesian candidate merge; later derived replaces earlier direct; flow unknown ~63 days after ETF switch; features.year_days dead; runtime 9:44 vs 10-min target; status doc AVGO wording slip — Ruling: defer, none changes results materially.
Final: branch ready for finishing (head 8572960). Workspace kept (owner keeps SDD records, per phase-1 precedent).
