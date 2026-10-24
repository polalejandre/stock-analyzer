"""Quality features (spec 5.2, without guidance tone) and their 0-1 sub-scores (spec 7.3).

"One year earlier" is the panel value `year_days` trading days earlier: the figure as it was
known then, so nothing here can look ahead."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules, ScoreRules
from analyzer.features.value import ebitda as _ebitda
from analyzer.panel.fundamentals import FundamentalPanels


def _f(f: FundamentalPanels, name: str) -> pd.DataFrame:
    return f[name].astype("float64")


def _growth(now: pd.DataFrame, before: pd.DataFrame) -> pd.DataFrame:
    return (now / before - 1).where(before > 0)


def _scaled(x: pd.DataFrame, scale: float) -> pd.DataFrame:
    """0.5 at zero, 1.0 at +scale, 0.0 at -scale."""
    return (0.5 + x / (2 * scale)).clip(0, 1)


def balance_score(nd_ebitda: pd.DataFrame, cap: float) -> pd.DataFrame:
    """1.0 at net debt / EBITDA <= 1, 0.0 at the cap or above, linear between."""
    return (1 - (nd_ebitda - 1) / (cap - 1)).clip(0, 1).where(nd_ebitda.notna())


@dataclass(frozen=True)
class QualityFeatures:
    revenue_growth: pd.DataFrame
    revenue_q_growth: pd.DataFrame
    eps_growth: pd.DataFrame
    revenue_decline_2q: pd.DataFrame
    revenue_decline_known: pd.DataFrame
    margin_change: pd.DataFrame
    fcf: pd.DataFrame
    fcf_prev: pd.DataFrame
    nd_ebitda: pd.DataFrame
    roe_change: pd.DataFrame
    growth_score: pd.DataFrame
    margin_score: pd.DataFrame
    roe_score: pd.DataFrame
    fcf_score: pd.DataFrame


def quality_features(f: FundamentalPanels, net_debt: pd.DataFrame, features: FeatureRules,
                     score: ScoreRules) -> QualityFeatures:
    year = features.year_days
    rev = _f(f, "revenue_ttm")
    revenue_growth = _growth(rev, rev.shift(year))
    revenue_q_growth = _growth(_f(f, "revenue_q0"), _f(f, "revenue_q0_ly"))
    eps = _f(f, "eps_ttm")
    eps_prev = eps.shift(year)
    eps_growth = ((eps - eps_prev) / eps_prev.abs()).where(eps_prev != 0).clip(-1, 1)
    decline = ((_f(f, "revenue_q0") < _f(f, "revenue_q0_ly"))
               & (_f(f, "revenue_q1") < _f(f, "revenue_q1_ly")))
    # decline is False when an input is missing; this says whether the rule could be evaluated
    decline_known = (_f(f, "revenue_q0").notna() & _f(f, "revenue_q0_ly").notna()
                     & _f(f, "revenue_q1").notna() & _f(f, "revenue_q1_ly").notna())

    margin = (_f(f, "operating_income_ttm") / rev).where(rev > 0)
    margin_change = margin - margin.shift(year)

    fcf = _f(f, "ocf_ttm") - _f(f, "capex_ttm")
    fcf_prev = fcf.shift(year)

    ebitda = _ebitda(f)
    with np.errstate(divide="ignore", invalid="ignore"):
        nd = np.where(ebitda > 0, net_debt / ebitda, np.where(net_debt > 0, np.inf, 0.0))
    nd_ebitda = pd.DataFrame(nd, index=rev.index, columns=rev.columns).where(
        ebitda.notna() & net_debt.notna())

    eq = _f(f, "equity_avg")
    roe = (_f(f, "net_income_ttm") / eq).where(eq > 0)
    roe_change = roe - roe.shift(year)

    with warnings.catch_warnings():  # an all-NaN slice gives NaN, which is what we want
        warnings.simplefilter("ignore", RuntimeWarning)
        growth_x = np.nanmean(np.stack([revenue_growth.to_numpy(), revenue_q_growth.to_numpy(),
                                        eps_growth.to_numpy()]), axis=0)
    growth_score = _scaled(pd.DataFrame(growth_x, index=rev.index, columns=rev.columns),
                           score.growth_scale)
    rising = fcf >= fcf_prev  # only meaningful where both are known; NaN score otherwise
    fcf_score = pd.DataFrame(np.where(fcf > 0, np.where(rising, 1.0, 0.5),
                                      np.where(rising, 0.25, 0.0)),
                             index=rev.index, columns=rev.columns).where(fcf.notna() & fcf_prev.notna())

    def f32(x: pd.DataFrame) -> pd.DataFrame:  # full-universe panels: halve the memory
        return x.astype("float32")

    return QualityFeatures(
        revenue_growth=f32(revenue_growth), revenue_q_growth=f32(revenue_q_growth),
        eps_growth=f32(eps_growth), revenue_decline_2q=decline.astype(bool),
        revenue_decline_known=decline_known.astype(bool),
        margin_change=f32(margin_change), fcf=f32(fcf), fcf_prev=f32(fcf_prev),
        nd_ebitda=f32(nd_ebitda), roe_change=f32(roe_change), growth_score=f32(growth_score),
        margin_score=f32(_scaled(margin_change, score.margin_scale)),
        roe_score=f32(_scaled(roe_change, score.roe_scale)), fcf_score=f32(fcf_score))
