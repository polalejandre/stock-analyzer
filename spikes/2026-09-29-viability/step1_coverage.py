# THROWAWAY: measure EDGAR quarterly coverage per ticker.
import pandas as pd
from common import UNIVERSE
from fundamentals import quarterly_table, snapshot

rows = []
for t in UNIVERSE:
    try:
        s = quarterly_table(t)
    except Exception as e:
        rows.append(dict(ticker=t, error=str(e)[:60]))
        continue
    r = dict(ticker=t)
    for k in ("revenue", "net_income", "eps", "op_income", "ocf", "capex"):
        d = s.get(k)
        r[k] = f"{len(d)}q {d['end'].min():%Y}-{d['end'].max():%y%m}" if d is not None and len(d) else "-"
    snap = snapshot(s, pd.Timestamp.today())
    r["latest_q"] = snap["q_end"].date() if snap["q_end"] is not None else None
    r["rev_yoy"] = round(snap["rev_yoy"], 3) if snap["rev_yoy"] is not None else None
    r["fcf_ok"] = snap["fcf_ttm"] is not None
    # lag between quarter end and filing for latest revenue quarter
    rv = s.get("revenue")
    if rv is not None and len(rv):
        last = rv.iloc[-1]
        r["file_lag_d"] = (last["filed"] - last["end"]).days
    rows.append(r)

df = pd.DataFrame(rows)
pd.set_option("display.width", 250, "display.max_columns", 20, "display.max_rows", 100)
print(df.to_string(index=False))
print("\nTTM revenue YoY computable today:", df["rev_yoy"].notna().sum(), "/", len(df))
print("FCF computable today:", df["fcf_ok"].sum(), "/", len(df))
print("Median filing lag (days):", df["file_lag_d"].median())
