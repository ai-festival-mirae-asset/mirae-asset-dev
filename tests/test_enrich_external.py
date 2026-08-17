import unittest

import pandas as pd

from pipeline.enrich_external import EXTERNAL_SOURCE, build


def domestic_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pd_itm_no": ["KR7000000001", "KR7000000002", "KR7000000003", "KRG000000004"],
            "instrument_type": ["etf", "etf", "etf", "etn"],
            # 관측 양수 / 공식 0(미수집 대체값) / 결측 / 결측
            "cu_charge_rt": [0.15, 0.0, None, None],
            "expense_ratio": [0.15, None, None, None],
            "cu_base_index": ["KOSPI200", None, None, None],
        }
    )


def krx_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "item_id": ["KR7000000001", "KR7000000002", "KR7000000003", "KRG000000004"],
            "instrument_type_external": ["etf", "etf", "etf", "etn"],
            "external_expense_ratio": [0.20, 0.12, 0.30, 0.0],
            "external_base_index": ["S&P500", "KRX 반도체", None, "Indxx Index"],
            "external_index_provider": ["S&P", "KRX", None, "Indxx"],
            "external_asset_class": ["주식"] * 4,
            "external_market_scope": ["해외", "국내", "국내", "해외"],
            "external_leverage_kind": ["일반"] * 4,
            "external_final_trade_date": [None, None, None, "2035-11-21"],
            "external_source": [EXTERNAL_SOURCE] * 4,
            "external_as_of": ["2026-08-06"] * 4,
        }
    )


class ExternalEnrichmentUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference, self.summary = build(domestic_frame(), krx_frame())

    def test_official_observed_value_wins(self) -> None:
        row = self.reference.iloc[0]
        self.assertEqual(row["expense_ratio_resolved"], 0.15)
        self.assertEqual(row["expense_ratio_resolved_source"], "official_snapshot")

    def test_official_zero_is_filled_from_external_and_flagged(self) -> None:
        row = self.reference.iloc[1]
        self.assertEqual(row["expense_ratio_resolved"], 0.12)
        self.assertEqual(row["expense_ratio_resolved_source"], EXTERNAL_SOURCE)
        self.assertTrue(bool(row["expense_ratio_zero_refuted"]))

    def test_conflict_is_flagged_without_changing_official(self) -> None:
        row = self.reference.iloc[0]
        self.assertTrue(bool(row["expense_ratio_conflict"]))
        self.assertEqual(row["expense_ratio_resolved"], 0.15)

    def test_missing_official_is_filled_from_external(self) -> None:
        row = self.reference.iloc[2]
        self.assertEqual(row["expense_ratio_resolved"], 0.30)
        self.assertEqual(row["expense_ratio_resolved_source"], EXTERNAL_SOURCE)

    def test_official_base_index_is_never_replaced(self) -> None:
        row = self.reference.iloc[0]
        self.assertEqual(row["base_index_resolved"], "KOSPI200")
        self.assertEqual(row["base_index_resolved_source"], "official_snapshot")
        self.assertTrue(bool(row["base_index_conflict"]))

    def test_etn_maturity_comes_from_external_with_source(self) -> None:
        row = self.reference.iloc[3]
        self.assertEqual(row["maturity_date_external"], "2035-11-21")
        self.assertEqual(row["maturity_source"], EXTERNAL_SOURCE)

    def test_summary_counts(self) -> None:
        fee = self.summary["expense_ratio"]
        self.assertEqual(fee["official_observed"], 1)
        self.assertEqual(fee["filled_from_external"], 3)
        self.assertEqual(fee["zero_refuted_by_external"], 1)
        self.assertTrue(self.summary["all_assertions_passed"])


if __name__ == "__main__":
    unittest.main()
