from analyzer.data.edgar.companyfacts import extract_facts


def _cf(tag, unit_rows):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": unit_rows}}}}}


def test_bank_and_property_concepts_are_extracted():
    q = {"start": "2024-01-01", "end": "2024-03-31", "val": 10.0, "filed": "2024-05-01",
         "form": "10-Q", "accn": "a1"}
    inst = {"end": "2024-03-31", "val": 99.0, "filed": "2024-05-01", "form": "10-Q", "accn": "a1"}
    rows = (extract_facts(_cf("InterestIncomeExpenseNet", [q]), 1)
            + extract_facts(_cf("NoninterestIncome", [q]), 1)
            + extract_facts(_cf("RealEstateInvestmentPropertyNet", [inst]), 1))
    concepts = {r[1] for r in rows}
    assert concepts == {"net_interest_income", "noninterest_income", "real_estate_property"}
