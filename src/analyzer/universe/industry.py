"""Map a stock to its sector ETF and, when one exists, its industry ETF."""

from __future__ import annotations

from dataclasses import dataclass

SECTOR_ETFS = {
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Telecommunication Services": "XLC",   # GICS name before 2018
    "Telecommunications Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

SUB_INDUSTRY_ETFS = {
    "Semiconductors": "SMH",
    "Semiconductor Materials & Equipment": "SMH",
    "Semiconductor Equipment": "SMH",
    "Application Software": "IGV",
    "Systems Software": "IGV",
    "Biotechnology": "XBI",
    "Pharmaceuticals": "XPH",
    "Regional Banks": "KRE",
    "Diversified Banks": "KBE",
    "Homebuilding": "ITB",
    "Oil & Gas Exploration & Production": "XOP",
    "Oil & Gas Equipment & Services": "OIH",
    "Aerospace & Defense": "ITA",
    "Health Care Equipment": "IHI",
    "Health Care Supplies": "IHI",
    "Apparel Retail": "XRT",
    "Broadline Retail": "XRT",
    "Home Improvement Retail": "XRT",
    "Other Specialty Retail": "XRT",
    "Computer & Electronics Retail": "XRT",
    "Automotive Retail": "XRT",
    "Steel": "XME",
    "Diversified Metals & Mining": "XME",
    "Copper": "XME",
    "Aluminum": "XME",
    "Gold": "GDX",
    "Passenger Airlines": "JETS",
    "Property & Casualty Insurance": "KIE",
    "Life & Health Insurance": "KIE",
    "Multi-line Insurance": "KIE",
    "Reinsurance": "KIE",
    "Insurance Brokers": "KIE",
    "Airlines": "JETS",
    "Internet & Direct Marketing Retail": "XRT",
    "Specialty Stores": "XRT",
}

# (first SIC, last SIC, GICS-style sector, industry ETF). First match wins: specific rules first.
SIC_RULES: list[tuple[int, int, str | None, str | None]] = [
    (6770, 6770, None, None),
    (3674, 3674, "Information Technology", "SMH"),
    (7372, 7372, "Information Technology", "IGV"),
    (2836, 2836, "Health Care", "XBI"),
    (2834, 2834, "Health Care", "XPH"),
    (2830, 2836, "Health Care", None),
    (2840, 2844, "Consumer Staples", None),
    (3841, 3845, "Health Care", "IHI"),
    (3840, 3851, "Health Care", None),
    (6021, 6021, "Financials", "KBE"),
    (6022, 6022, "Financials", "KRE"),
    (6311, 6411, "Financials", "KIE"),
    (6500, 6599, "Real Estate", None),
    (6798, 6798, "Real Estate", None),
    (1311, 1311, "Energy", "XOP"),
    (1381, 1389, "Energy", "OIH"),
    (1220, 1241, "Energy", None),
    (1040, 1040, "Materials", "GDX"),
    (1000, 1099, "Materials", "XME"),
    (1400, 1499, "Materials", None),
    (3310, 3399, "Materials", "XME"),
    (1531, 1531, "Consumer Discretionary", "ITB"),
    (3720, 3729, "Industrials", "ITA"),
    (3760, 3769, "Industrials", "ITA"),
    (4512, 4512, "Industrials", "JETS"),
    (4953, 4959, "Industrials", None),
    (3651, 3651, "Consumer Discretionary", None),
    (3710, 3716, "Consumer Discretionary", None),
    (3730, 3799, "Industrials", None),
    (3570, 3579, "Information Technology", None),
    (3600, 3659, "Industrials", None),
    (3660, 3699, "Information Technology", None),
    (7370, 7379, "Information Technology", None),
    (7300, 7369, "Industrials", None),
    (7380, 7389, "Industrials", None),
    (1300, 1399, "Energy", None),
    (2900, 2999, "Energy", None),
    (4900, 4999, "Utilities", None),
    (4800, 4899, "Communication Services", None),
    (2000, 2199, "Consumer Staples", None),
    (5122, 5122, "Health Care", None),
    (5140, 5149, "Consumer Staples", None),
    (5000, 5199, "Industrials", None),
    (5331, 5331, "Consumer Staples", None),
    (5400, 5499, "Consumer Staples", None),
    (5812, 5812, "Consumer Discretionary", None),
    (5912, 5912, "Consumer Staples", None),
    (5200, 5999, "Consumer Discretionary", "XRT"),
    (2200, 2399, "Consumer Discretionary", None),
    (2400, 2499, "Materials", None),
    (2500, 2599, "Consumer Discretionary", None),
    (2600, 2699, "Materials", None),
    (2700, 2799, "Communication Services", None),
    (3000, 3099, "Materials", None),
    (3100, 3199, "Consumer Discretionary", None),
    (3200, 3299, "Materials", None),
    (3800, 3899, "Information Technology", None),
    (3900, 3999, "Consumer Discretionary", None),
    (7000, 7099, "Consumer Discretionary", None),
    (7200, 7299, "Consumer Discretionary", None),
    (7500, 7599, "Consumer Discretionary", None),
    (7812, 7829, "Communication Services", None),
    (7900, 7999, "Consumer Discretionary", None),
    (8100, 8199, "Industrials", None),
    (8200, 8299, "Consumer Discretionary", None),
    (8700, 8799, "Industrials", None),
    (2800, 2899, "Materials", None),
    (8000, 8099, "Health Care", None),
    (6000, 6799, "Financials", None),
    (4000, 4799, "Industrials", None),
    (3400, 3599, "Industrials", None),
    (1500, 1799, "Industrials", None),
]

ALL_REFERENCE_ETFS = sorted(
    set(SECTOR_ETFS.values()) | set(SUB_INDUSTRY_ETFS.values())
    | {etf for *_, etf in SIC_RULES if etf} | {"SPY"}
)


@dataclass(frozen=True)
class ReferenceEtfs:
    sector_etf: str | None
    industry_etf: str | None


def _from_sic(sic: str | None) -> tuple[str | None, str | None]:
    if not sic or not str(sic).isdigit():
        return None, None
    code = int(sic)
    for lo, hi, sector, etf in SIC_RULES:
        if lo <= code <= hi:
            return sector, etf
    return None, None


def reference_etfs(gics_sector: str | None, gics_sub_industry: str | None,
                   sic: str | None) -> ReferenceEtfs:
    if isinstance(gics_sector, str) and gics_sector:
        return ReferenceEtfs(SECTOR_ETFS.get(gics_sector),
                             SUB_INDUSTRY_ETFS.get(gics_sub_industry if isinstance(gics_sub_industry, str) else ""))
    sector, etf = _from_sic(sic)
    return ReferenceEtfs(SECTOR_ETFS.get(sector) if sector else None, etf)
