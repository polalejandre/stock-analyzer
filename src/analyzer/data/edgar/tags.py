"""Engine concepts and the XBRL tags that can carry them, in priority order."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "10-KT/A"}


@dataclass(frozen=True)
class Concept:
    name: str
    kind: Literal["duration", "instant"]
    unit: str
    tags: tuple[str, ...]
    # Partial lines that some companies report instead of a total: summed per filing when that
    # filing has none of `tags` for the period, and checked against the company's own totals
    # from other filings (see companyfacts._fallback_rows).
    fallback_tags: tuple[str, ...] = ()


def _g(*names: str) -> tuple[str, ...]:
    return tuple(n if ":" in n else f"us-gaap:{n}" for n in names)


CONCEPTS: tuple[Concept, ...] = (
    Concept("revenue", "duration", "USD", _g(
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
        "RevenuesNetOfInterestExpense", "RegulatedAndUnregulatedOperatingRevenue",
        "RevenueFromContractWithCustomerIncludingAssessedTax"),
        fallback_tags=_g("SalesRevenueGoodsNet", "SalesRevenueServicesNet")),
    Concept("cost_of_revenue", "duration", "USD", _g(
        "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold")),
    Concept("gross_profit", "duration", "USD", _g("GrossProfit")),
    Concept("operating_income", "duration", "USD", _g("OperatingIncomeLoss")),
    Concept("net_income", "duration", "USD", _g("NetIncomeLoss", "ProfitLoss")),
    Concept("eps_diluted", "duration", "USD/shares", _g(
        "EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")),
    Concept("operating_cash_flow", "duration", "USD", _g(
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")),
    Concept("capex", "duration", "USD", _g(
        "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
        fallback_tags=_g(
            "PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsToAcquireOtherProductiveAssets",
            "PaymentsForCapitalImprovements", "PaymentsToDevelopRealEstateAssets",
            "PaymentsToExploreAndDevelopOilAndGasProperties",
            "PaymentsToAcquireOilAndGasPropertyAndEquipment", "PaymentsForConstructionInProcess")),
    Concept("depreciation_amortization", "duration", "USD", _g(
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet", "Depreciation")),
    Concept("diluted_shares", "duration", "shares", _g(
        "WeightedAverageNumberOfDilutedSharesOutstanding")),
    Concept("cash", "instant", "USD", _g(
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")),
    Concept("long_term_debt", "instant", "USD", _g("LongTermDebtNoncurrent", "LongTermDebt")),
    Concept("short_term_debt", "instant", "USD", _g(
        "LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper")),
    Concept("stockholders_equity", "instant", "USD", _g(
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")),
    Concept("total_assets", "instant", "USD", _g("Assets")),
    Concept("shares_outstanding", "instant", "shares", _g(
        "dei:EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")),
)

CONCEPT_BY_NAME = {c.name: c for c in CONCEPTS}
# Bump when companyfacts.extract_facts changes how it reads the tags (e.g. the fallback rule).
EXTRACTION_RULES = 4
# Changes with any concept, tag, fallback or extraction rule; stored facts are then re-extracted.
TAG_MAP_FINGERPRINT = hashlib.sha256(
    f"{EXTRACTION_RULES}:{CONCEPTS!r}".encode()).hexdigest()[:16]
CONCEPT_BY_TAG = {tag: c for c in CONCEPTS for tag in c.tags}
