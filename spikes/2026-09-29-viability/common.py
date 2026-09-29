# THROWAWAY SPIKE CODE — not the real project.
import json, os, time, pathlib, requests

ROOT = pathlib.Path(__file__).parent
CACHE = ROOT / "cache"
CACHE.mkdir(exist_ok=True)
# SEC requires a contact email in the User-Agent: export SEC_USER_AGENT="YourApp you@example.com"
UA = {"User-Agent": os.environ["SEC_USER_AGENT"]}

# ~40 tickers across sectors, sector ETF per ticker
UNIVERSE = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "ADBE": "XLK", "CRM": "XLK", "INTC": "XLK",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "NKE": "XLY", "SBUX": "XLY", "HD": "XLY", "LULU": "XLY", "DECK": "XLY",
    "JPM": "XLF", "BAC": "XLF", "V": "XLF", "MA": "XLF", "PYPL": "XLF",
    "UNH": "XLV", "JNJ": "XLV", "PFE": "XLV", "LLY": "XLV", "ABBV": "XLV",
    "XOM": "XLE", "CVX": "XLE",
    "PG": "XLP", "KO": "XLP", "PEP": "XLP", "COST": "XLP", "EL": "XLP",
    "CAT": "XLI", "BA": "XLI", "UPS": "XLI",
    "PLD": "XLRE", "O": "XLRE",
    "NEE": "XLU",
    "LIN": "XLB",
}
ETFS = sorted(set(UNIVERSE.values()) | {"SPY"})


def sec_get(url, cache_name):
    p = CACHE / cache_name
    if p.exists():
        return json.loads(p.read_text())
    time.sleep(0.15)  # stay well under SEC's 10 req/s
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    p.write_text(r.text)
    return r.json()


def sec_get_text(url, cache_name):
    p = CACHE / cache_name
    if p.exists():
        return p.read_text()
    time.sleep(0.15)
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    p.write_text(r.text)
    return r.text


def cik_map():
    d = sec_get("https://www.sec.gov/files/company_tickers.json", "company_tickers.json")
    return {v["ticker"]: int(v["cik_str"]) for v in d.values()}
