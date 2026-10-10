import pytest

from analyzer.universe.industry import ALL_REFERENCE_ETFS, ReferenceEtfs, reference_etfs


@pytest.mark.parametrize(
    "sector, sub, sic, expected",
    [
        ("Information Technology", "Semiconductors", None, ReferenceEtfs("XLK", "SMH")),
        ("Information Technology", "Technology Hardware, Storage & Peripherals", "3571",
         ReferenceEtfs("XLK", None)),
        ("Telecommunication Services", None, None, ReferenceEtfs("XLC", None)),
        ("Financials", "Regional Banks", "6022", ReferenceEtfs("XLF", "KRE")),
        (None, None, "3674", ReferenceEtfs("XLK", "SMH")),
        (None, None, "5412", ReferenceEtfs("XLP", None)),
        (None, None, "5651", ReferenceEtfs("XLY", "XRT")),
        (None, None, "6770", ReferenceEtfs(None, None)),
        (None, None, None, ReferenceEtfs(None, None)),
        (None, None, "2833", ReferenceEtfs("XLV", None)),
        (None, None, "2834", ReferenceEtfs("XLV", "XPH")),
        (None, None, "2844", ReferenceEtfs("XLP", None)),
        (None, None, "5812", ReferenceEtfs("XLY", None)),
        (None, None, "6021", ReferenceEtfs("XLF", "KBE")),
        (None, None, "6512", ReferenceEtfs("XLRE", None)),
        (None, None, "3714", ReferenceEtfs("XLY", None)),
        (None, None, "7011", ReferenceEtfs("XLY", None)),
        (None, None, "2711", ReferenceEtfs("XLC", None)),
        (float("nan"), float("nan"), "3674", ReferenceEtfs("XLK", "SMH")),
    ],
)
def test_reference_etfs(sector, sub, sic, expected):
    assert reference_etfs(sector, sub, sic) == expected


def test_all_reference_etfs_include_spy_and_sectors():
    assert {"SPY", "XLK", "SMH", "KRE"} <= set(ALL_REFERENCE_ETFS)
