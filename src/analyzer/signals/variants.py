"""Which scoring variant applies to each symbol (spec 7.4, addendum 5.3)."""

from __future__ import annotations

import pandas as pd

from analyzer.config import VariantRules
from analyzer.features.value import METRICS, VARIANT_METRICS
from analyzer.panel.fundamentals import FundamentalPanels

STANDARD, FINANCIALS, REIT = "standard", "financials", "reit"
SCORED = (STANDARD, FINANCIALS, REIT)


def variant_of(cik_known: bool, sic: str | None, has_property: bool, has_us_gaap: bool,
               rules: VariantRules) -> str:
    if not cik_known:
        return "excluded:no_cik"
    if not has_us_gaap:
        return "excluded:no_us_gaap"     # IFRS filers, or no XBRL financials at all
    if sic in rules.excluded_sics:
        return "excluded:fund"
    if sic == rules.reit.sic:
        return REIT if has_property else "excluded:mortgage_reit"
    if sic and sic.isdigit() and rules.financials.sic_min <= int(sic) <= rules.financials.sic_max:
        return FINANCIALS
    return STANDARD


def variant_series(entities: pd.DataFrame, f: FundamentalPanels, rules: VariantRules) -> pd.Series:
    return pd.Series({
        s: variant_of(pd.notna(entities.at[s, "cik"]), entities.at[s, "sic"],
                      bool(f.has_property.get(s, False)), bool(f.has_us_gaap.get(s, False)), rules)
        for s in entities.index}, dtype=object)


def columns_for_metrics(variants: pd.Series) -> dict[str, list[str]]:
    return {m: [s for s, v in variants.items() if m in VARIANT_METRICS.get(v, ())] for m in METRICS}
