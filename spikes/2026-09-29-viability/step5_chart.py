# THROWAWAY: static lightweight-charts preview of today's setups (architecture B look & feel).
import json, pandas as pd
from common import CACHE, UNIVERSE
from step4_dislocation import rsi  # noqa: re-runs step4 on import; fine for a spike

c, o, h, l = (pd.read_pickle(CACHE / f"{k}.pkl") for k in ("close", "open", "high", "low"))
today = pd.read_pickle(CACHE / "today.pkl").set_index("ticker")
PICKS = ["DECK", "NFLX", "CAT", "INTC", "HD"]


def div_points(px, r, recent=5, lookback=60, gap=15):
    p = px.iloc[-lookback:]
    cur, prev = p.iloc[-recent:], p.iloc[:-gap]
    return prev.idxmin(), cur.idxmin()


setups = {}
for t in PICKS:
    px = c[t].dropna()
    r = rsi(px)
    w = px.index[-260:]
    fmt = lambda d: d.strftime("%Y-%m-%d")
    candles = [dict(time=fmt(d), open=round(o[t][d], 2), high=round(h[t][d], 2), low=round(l[t][d], 2),
                    close=round(c[t][d], 2)) for d in w]
    snap = today.loc[t]
    price, hi = float(snap.price), float(snap.high63)
    drop = hi - price
    low20 = float(l[t].dropna().iloc[-20:].min())
    worst = float(px.pct_change().iloc[-63:].min())
    d_prev, d_cur = div_points(px, r)
    setups[t] = dict(
        candles=candles,
        rsi=[dict(time=fmt(d), value=round(float(r[d]), 1)) for d in w],
        levels=dict(entry=round(price, 2), stop=round(low20 * 0.97, 2),
                    t1=round(price + 0.382 * drop, 2), t2=round(price + 0.5 * drop, 2), high63=round(hi, 2)),
        div=dict(on=bool(snap.div_d), prev=fmt(d_prev), cur=fmt(d_cur),
                 p_prev=round(float(px[d_prev]), 2), p_cur=round(float(px[d_cur]), 2),
                 r_prev=round(float(r[d_prev]), 1), r_cur=round(float(r[d_cur]), 1)),
        facts=dict(sector=UNIVERSE[t], dd=float(snap.dd), rel=float(snap.rel), fund=snap.fund,
                   rev_yoy=snap.rev_yoy, rev_q_yoy=snap.rev_q_yoy, prof_yoy=snap.prof_yoy, ctx=snap.ctx,
                   worst_day=worst, div_w=bool(snap.div_w)),
    )

html = (pathlib := __import__("pathlib")).Path(__file__).with_name("preview_template.html").read_text()
out = pathlib.Path(__file__).with_name("preview.html")
out.write_text(html.replace("__DATA__", json.dumps(setups, default=float)))
print("wrote", out)
