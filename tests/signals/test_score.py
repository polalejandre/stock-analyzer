import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.signals.reasons import reasons
from analyzer.signals.score import score

R = load_rules()


def _row(**over):
    base = dict(variant="standard", median_pct=15.0, cheaper=2, n_metrics=4, correction=False,
                industry=1.0, accumulation=0.5, divergence=True, reclaimed=False,
                insider_cluster=False, growth_score=1.0, margin_score=0.5, roe_score=np.nan,
                fcf_score=1.0, nd_ebitda=0.5)
    base.update(over)
    return base


def test_components_and_total():
    s = score(pd.DataFrame([_row()]), R).iloc[0]
    assert s["c_value"] == 0.5 * 0.5 + 0.5 * 0.5            # (30-15)/30 and 2 of 4 cheaper
    assert s["c_quality"] == (1.0 + 0.5 + 1.0 + 1.0) / 4    # balance 1.0 at nd/ebitda 0.5
    assert s["c_dislocation"] == 0.7
    assert s["c_flow"] == 0.5 * 1.0 + 0.5 * 0.5
    assert s["c_technical"] == 0.5
    expected = 30 * 0.5 + 30 * 0.875 + 15 * 0.7 + 15 * 0.75 + 10 * 0.5
    assert abs(s["score"] - expected) < 1e-9
    assert s["tier"] == "B"


def test_financials_quality_uses_growth_and_roe_only():
    s = score(pd.DataFrame([_row(variant="financials", roe_score=0.0, margin_score=1.0,
                                 fcf_score=1.0)]), R).iloc[0]
    assert s["c_quality"] == 0.5


def test_insider_bonus_is_capped_at_100():
    row = _row(median_pct=0.0, cheaper=4, correction=True, reclaimed=True, accumulation=1.0,
               margin_score=1.0, insider_cluster=True)
    s = score(pd.DataFrame([row]), R).iloc[0]
    assert s["score"] == 100 and s["tier"] == "A"


def test_all_quality_parts_unknown_gives_zero_quality_and_defined_score():
    row = _row(growth_score=np.nan, margin_score=np.nan, fcf_score=np.nan, nd_ebitda=np.nan)
    s = score(pd.DataFrame([row]), R).iloc[0]
    assert s["c_quality"] == 0.0
    assert not np.isnan(s["score"])


def test_unknown_industry_scores_as_neutral():
    unknown = score(pd.DataFrame([_row(industry=np.nan)]), R).iloc[0]
    neutral = score(pd.DataFrame([_row(industry=0.0)]), R).iloc[0]
    assert unknown["c_flow"] == neutral["c_flow"] == 0.5 * R.score.flow_neutral + 0.5 * 0.5
    assert unknown["score"] == neutral["score"]


def test_reasons_mention_value_drop_and_skipped_rules():
    row = _row(pct_pe=12.0, pct_ev_ebitda=np.nan, pct_p_fcf=20.0, pct_ps=40.0, drawdown=0.22,
               gap=-0.15, ref_etf="SMH", buyers=0, executive_buy=False, missing_rules="fcf",
               annual=True, fallback=False)
    text = reasons(row, R)
    assert "P/E at 12th percentile of its 5-year range" in text
    assert any(r.startswith("Down 22% from its 63-day high") for r in text)
    assert "Value-trap rule skipped (no data): fcf" in text
    assert "TTM from the annual report (quarters missing)" in text
