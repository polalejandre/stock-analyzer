# Phase 2 kickoff notes (2026-10-11)

Phase 2 covers features, signals and the research harness. It ends with the go/no-go of spec
section 9.6. These notes record where brainstorming stopped, so the next session can resume.
There is no plan yet.

Inputs:

- `docs/specs/2026-10-10-stock-analyzer-design.md`, sections 5–9.
- `docs/plans/2026-10-10-phase-1-status.md`, sections "Phase-2 prep items", "Deferred to
  phase 2" and the review notes.

## Path

This is architectural work, because it adds new subsystems: `features/`, `signals/` and
`research/`. The design spec already covers it, so the steps are:

1. Settle the open decisions below with the owner, one at a time.
2. Write a short phase-2 design addendum that covers only what the spec leaves open or what
   phase 1 changed.
3. Have the owner review the addendum.
4. Write the implementation plan.

## Shared understanding (from the spec; owner to correct)

- **Features:** value, quality, dislocation, flow, technical and regime. They are pure
  functions of point-in-time data with an explicit `as_of` date.
- **Signals:** gates, score, tiers and plans. They read only the features and `rules.toml`,
  and give a reason string for every decision.
- **Research:** an event study over 2014 to today, using the same code as the future daily
  scan.
  - Thresholds are tuned on 2014–2021 only.
  - The go/no-go is judged on 2022 to today, with rules fixed in advance.
  - Every out-of-sample run is logged in `docs/research/oos-runs.csv`.
- **Go/no-go:** these four tests apply to group 3 (gated setups), excluding young listings:
  - n ≥ 100;
  - median 63-day excess return vs SPY > 0;
  - win rate ≥ 55% at 63 days;
  - a better median 63-day excess return than group 2 (all dislocated stocks).

  Separately, score quintiles must rank forward returns roughly in order.
- **Not part of the go/no-go** (spec 9.4): shorts, young listings and the watchlist.

## Open decision 1: scope before the first go/no-go (asked, not yet answered)

| Option | Builds before the first go/no-go | Trade-off |
|---|---|---|
| **A. Core first, then extras (recommended)** | Standard, financials and REIT variants; value, quality, dislocation, flow, technical and insider features; plans; score; the harness. | Fastest honest answer. Guidance tone is left out of that run, and that is recorded before any results are seen. Guidance tone, shorts, watchlist and the young buckets follow. |
| B. Full spec in one go | All of spec sections 5–9, including guidance tone, shorts, watchlist and the young variant. | Most complete, but the slowest to an answer. |
| C. Quick read first | Gates 1–3 against SPY and dislocated stocks only; no score or plans. | The fastest signal, but not the real go/no-go. |

Why A:

- Guidance tone needs the 8-K Item 2.02 press releases (EX-99.1). That is about 80,000
  documents, roughly 3 hours or more of SEC downloads, plus a keyword parser.
- It is one value-trap rule and one score sub-score. Leaving it out loosens the trap gate
  slightly; it does not change what the test measures.
- The omission must be written into `rules.toml` and the addendum before the first run, so
  the go/no-go stays "fixed in advance".

## Other decisions to settle after scope

1. **Survivorship.** 533 past index members have no prices: they were acquired, delisted or
   renamed (ANTM→ELV, ABC→COR). Options:
   - a ticker alias table for renames;
   - another price source for delisted names;
   - an explicit exclusion with the coverage reported (spec 9.3).
2. **TTM when quarters are missing** (status doc, finding 4). Build TTM from annual 10-K
   figures, or skip such tickers on those dates.
3. **Financials and REIT variants.** They need concepts phase 1 doesn't map yet:
   - financials: book value (P/B) and ROE;
   - REITs: FFO, approximated as net income + depreciation.
   Bank revenue was deferred from the audit.
4. **Compute strategy.** About 1,100 tickers × about 3,000 trading days is too much for full
   fundamentals on every day. The likely approach is to screen dislocation first from prices
   (cheap), then compute fundamentals only for candidate days. A 42-day cooldown per ticker
   applies (spec 9.5).
5. **Fallback hindsight** (from the review). Whether a company's fallback tags are used is
   decided with later filings. No value is known before its `filed` date, but phase 2 should
   say whether that is acceptable for research, or whether fallback-only companies should be
   marked "unverified".

## Next step

Answer open decision 1. Then continue with the decisions in the list above.
