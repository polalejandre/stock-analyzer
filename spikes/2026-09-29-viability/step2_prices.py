# THROWAWAY: prices from yfinance + Stooq fallback test.
import io, requests, pandas as pd, yfinance as yf
from common import UNIVERSE, ETFS, CACHE
tickers = list(UNIVERSE) + ETFS
df = yf.download(tickers, start="2016-01-01", auto_adjust=True, progress=False, threads=True)
close = df["Close"]; close.to_parquet(CACHE / "close.parquet") if False else close.to_pickle(CACHE / "close.pkl")
df["Low"].to_pickle(CACHE / "low.pkl"); df["High"].to_pickle(CACHE / "high.pkl"); df["Open"].to_pickle(CACHE / "open.pkl"); df["Volume"].to_pickle(CACHE / "vol.pkl")
print("yfinance shape", close.shape, "last date", close.index[-1].date())
print("missing tickers:", [t for t in tickers if close[t].dropna().empty])
print("first valid dates (late starters):", {t: str(close[t].first_valid_index().date()) for t in tickers if close[t].first_valid_index() > pd.Timestamp("2016-02-01")})
for sym in ["aapl.us", "nflx.us"]:
    try:
        r = requests.get(f"https://stooq.com/q/d/l/?s={sym}&i=d", timeout=20)
        txt = r.text[:200].replace("\n", " | ")
        print("stooq", sym, r.status_code, txt[:150])
    except Exception as e:
        print("stooq", sym, "ERR", e)
