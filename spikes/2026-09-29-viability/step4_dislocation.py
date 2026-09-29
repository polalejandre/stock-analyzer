# THROWAWAY: dislocation rule — historical recovery study + today's scan.
import numpy as np, pandas as pd
from common import UNIVERSE, CACHE
from fundamentals import quarterly_table, snapshot

close = pd.read_pickle(CACHE / "close.pkl")
spy = close["SPY"]
DD_MIN, REL_MIN, COOLDOWN = -0.15, -0.10, 42


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def bullish_div(px, r, recent=5, lookback=60, gap=15):
    """Lower low in price vs an earlier swing low, with a higher RSI low. Uses data up to the last bar only."""
    if len(px) < lookback:
        return False
    p, rr = px.iloc[-lookback:], r.iloc[-lookback:]
    cur, prev = p.iloc[-recent:], p.iloc[:-gap]
    i_cur, i_prev = cur.idxmin(), prev.idxmin()
    return bool(cur.min() < prev.min() and rr[i_cur] > rr[i_prev] + 3 and rr[i_cur] < 45)


def fund_bucket(f):
    if f["rev_yoy"] is None:
        return "unknown"
    prof = f["oi_yoy"] if f["oi_yoy"] is not None else f["ni_yoy"]
    q = f["rev_q_yoy"]
    if f["rev_yoy"] > 0 and (q is None or q > 0) and (prof is None or prof > -0.05):
        return "intact"
    return "weakening"


def context(t_idx, sec):
    spy_dd = spy.iloc[t_idx] / spy.iloc[max(0, t_idx - 63):t_idx + 1].max() - 1
    sec_ret = sec.iloc[t_idx] / sec.iloc[max(0, t_idx - 63)] - 1
    if spy_dd <= -0.08:
        return "market-correction"
    if sec_ret <= -0.10:
        return "sector-driven"
    return "idiosyncratic"


def evaluate(t, px, sec, r_d, r_w, i, fund):
    hi = px.iloc[max(0, i - 63):i + 1].max()
    dd = px.iloc[i] / hi - 1
    rel = (px.iloc[i] / px.iloc[max(0, i - 63)] - 1) - (sec.iloc[i] / sec.iloc[max(0, i - 63)] - 1)
    d = px.index[i]
    f = snapshot(fund, d) if fund is not None else None
    wk = px.iloc[:i + 1].resample("W-FRI").last()
    return dict(ticker=t, date=d, price=px.iloc[i], high63=hi, dd=dd, rel=rel,
                fund=fund_bucket(f) if f else "unknown", rev_yoy=f and f["rev_yoy"], rev_q_yoy=f and f["rev_q_yoy"],
                prof_yoy=f and (f["oi_yoy"] if f["oi_yoy"] is not None else f["ni_yoy"]),
                ctx=context(i, sec), div_d=bullish_div(px.iloc[:i + 1], r_d.iloc[:i + 1]),
                div_w=bullish_div(wk, rsi(wk), recent=3, lookback=30, gap=6))


events, today = [], []
for t, etf in UNIVERSE.items():
    px = close[t].dropna()
    sec = close[etf].reindex(px.index).ffill()
    if sec.isna().all() or sec.first_valid_index() > px.index[0]:
        sec = sec.fillna(spy.reindex(px.index))  # XLC before 2018-06
    try:
        fund = quarterly_table(t)
    except Exception:
        fund = None
    r_d = rsi(px)
    pos = {d: k for k, d in enumerate(px.index)}
    last_evt = -10**9
    fridays = [d for d in px.index if d.weekday() == 4 and d >= pd.Timestamp("2018-01-01")]
    for d in fridays:
        i = pos[d]
        if i - last_evt < COOLDOWN or i + 63 >= len(px):
            continue
        hi = px.iloc[max(0, i - 63):i + 1].max()
        if px.iloc[i] / hi - 1 > DD_MIN:
            continue
        e = evaluate(t, px, sec, r_d, None, i, fund)
        if e["rel"] > REL_MIN:
            continue
        last_evt = i
        p0 = px.iloc[i]
        for h in (10, 21, 63):
            e[f"ret{h}"] = px.iloc[i + h] / p0 - 1
            e[f"xs{h}"] = e[f"ret{h}"] - (spy.iloc[spy.index.get_loc(d) + h] / spy.loc[d] - 1)
        fut = px.iloc[i + 1:i + 64]
        e["rec50_63"] = fut.max() >= p0 + 0.5 * (hi - p0)
        e["mae21"] = px.iloc[i + 1:i + 22].min() / p0 - 1
        events.append(e)
    today.append(evaluate(t, px, sec, r_d, None, len(px) - 1, fund))

ev = pd.DataFrame(events)
ev.to_pickle(CACHE / "events.pkl")
pd.DataFrame(today).to_pickle(CACHE / "today.pkl")


def summary(g):
    return pd.Series(dict(n=len(g), win21=(g.ret21 > 0).mean(), med_ret21=g.ret21.median(), med_xs21=g.xs21.median(),
                          win63=(g.ret63 > 0).mean(), med_ret63=g.ret63.median(), med_xs63=g.xs63.median(),
                          rec50_63=g.rec50_63.mean(), med_mae21=g.mae21.median()))


pd.set_option("display.width", 220, "display.float_format", "{:.3f}".format)
print(f"events 2018-now: {len(ev)} across {ev.ticker.nunique()} tickers\n")
print("ALL:\n", summary(ev).to_frame().T.to_string())
print("\nBY FUNDAMENTALS:\n", ev.groupby("fund").apply(summary, include_groups=False).to_string())
print("\nINTACT, BY CONTEXT:\n", ev[ev.fund == "intact"].groupby("ctx").apply(summary, include_groups=False).to_string())
ev["any_div"] = ev.div_d | ev.div_w
print("\nINTACT, BY RSI DIVERGENCE (daily or weekly):\n",
      ev[ev.fund == "intact"].groupby("any_div").apply(summary, include_groups=False).to_string())
print("\nWEAKENING, BY RSI DIVERGENCE:\n",
      ev[ev.fund == "weakening"].groupby("any_div").apply(summary, include_groups=False).to_string())
