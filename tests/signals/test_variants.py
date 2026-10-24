import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.panel.fundamentals import FundamentalPanels
from analyzer.signals.variants import columns_for_metrics, variant_of, variant_series

V = load_rules().variants


def test_variant_rules():
    assert variant_of(True, "3674", False, True, V) == "standard"
    assert variant_of(True, "6022", False, True, V) == "financials"
    assert variant_of(True, "6411", False, True, V) == "financials"
    assert variant_of(True, "6798", True, True, V) == "reit"
    assert variant_of(True, "6798", False, True, V) == "excluded:mortgage_reit"
    assert variant_of(True, "6726", False, True, V) == "excluded:fund"
    assert variant_of(True, "3674", False, False, V) == "excluded:no_us_gaap"
    assert variant_of(False, None, False, False, V) == "excluded:no_cik"


def test_columns_for_metrics():
    v = pd.Series({"A": "standard", "B": "financials", "C": "reit", "D": "excluded:fund"})
    cols = columns_for_metrics(v)
    assert cols["pe"] == ["A", "B"]
    assert cols["ev_ebitda"] == ["A", "C"]
    assert cols["pb"] == ["B"] and cols["p_ffo"] == ["C"]


def test_variant_series_from_entities_and_static_panels():
    entities = pd.DataFrame(
        {"cik": [1.0, np.nan, 3.0, 4.0, 5.0], "sic": ["3674", None, "6798", "6798", "6022"]},
        index=["A", "B", "C", "D", "E"],
    )
    f = FundamentalPanels(
        frames={},
        first_filed=pd.Series(dtype="datetime64[ns]"),
        has_property=pd.Series({"A": False, "B": False, "C": True, "D": False, "E": False}),
        has_us_gaap=pd.Series({"A": True, "B": True, "C": True, "D": True, "E": False}),
    )
    got = variant_series(entities, f, V)
    assert list(got.index) == ["A", "B", "C", "D", "E"]
    assert got.to_dict() == {
        "A": "standard",
        "B": "excluded:no_cik",
        "C": "reit",
        "D": "excluded:mortgage_reit",
        "E": "excluded:no_us_gaap",
    }
