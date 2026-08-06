import unittest

import pandas as pd

from pipeline.build_unified_view import (
    UNIFIED_COLUMNS,
    from_bond,
    from_domestic_etp,
    from_fund,
    from_overseas_etp,
)


def bond_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pd_no": ["KR1234567890"],
            "pd_nm": ["국민주택1종채권 20-01"],
            "risk_grade": [6],
            "risk_available": ["True"],
            "quality_status": ["usable"],
            "default_search_eligible": ["True"],
            "source_origin": ["official_snapshot"],
            "source_row_number": [2],
        }
    )


def domestic_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pd_itm_no": ["KR7069500007", "KRG500000846", "KR7000000009"],
            "pd_nm": ["활성 ETF", "활성 ETN", "종료 ETF"],
            "instrument_type": ["etf", "etn", "etf"],
            "wu_inv_ast_type": ["주식", "채권", "주식"],
            "wu_inv_rgn": ["국내", "국내", "국내"],
            "risk_grade": [2, 2, 2],
            "risk_available": ["True", "True", "True"],
            "expense_ratio": [0.15, None, None],
            "expense_ratio_available": ["True", "False", "False"],
            "aum": [100.0, 200.0, None],
            "aum_available": ["True", "True", "False"],
            "du_er_1y": [5.5, 3.0, -100.0],
            "listing_status": ["active_open_ended", "active_open_ended", "ended_before_extract"],
            "cu_upt_dt": ["2026-06-15", "2026-06-15", None],
            "du_upt_dt": ["2026-06-15", "2026-06-15", None],
            "quality_status": ["usable"] * 3,
            "default_search_eligible": ["True", "True", "False"],
            "source_origin": ["official_snapshot"] * 3,
            "source_row_number": [2, 3, 4],
        }
    )


def overseas_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pd_itm_no": ["SPY.K"],
            "pd_nm": ["SPDR S&P 500"],
            "instrument_type": ["etf"],
            "wu_inv_ast_type": ["Equity"],
            "wu_inv_rgn": ["United States of America"],
            "risk_grade": [None],
            "risk_available": ["False"],
            "expense_ratio": [0.09],
            "expense_ratio_available": ["True"],
            "aum": [500.0],
            "aum_available": ["True"],
            "cu_upt_dt": ["2026-06-14"],
            "du_upt_dt": ["2026-06-16"],
            "du_nav_base_dt": ["2026-06-14"],
            "quality_status": ["usable"],
            "default_search_eligible": ["True"],
            "source_origin": ["official_snapshot"],
            "source_row_number": [2],
        }
    )


def fund_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "itm_no": ["K55301CE3408"],
            "itm_nm": ["어떤증권투자신탁"],
            "or_attr_desc": ["주식형"],
            "fd_ivst_rgn_desc": ["국내"],
            "risk_grade": [3],
            "risk_available": ["True"],
            "fd_nast_suma": [1000.0],
            "fd_yr1_ern_r": [7.2],
            "quality_status": ["usable"],
            "default_search_eligible": ["True"],
            "source_origin": ["official_snapshot"],
            "source_row_number": [2],
        }
    )


class UnifiedViewUnitTests(unittest.TestCase):
    def test_every_adapter_returns_the_same_contract(self) -> None:
        for adapter, frame in (
            (from_bond, bond_frame()),
            (from_domestic_etp, domestic_frame()),
            (from_overseas_etp, overseas_frame()),
            (from_fund, fund_frame()),
        ):
            with self.subTest(adapter=adapter.__name__):
                self.assertEqual(list(adapter(frame).columns), UNIFIED_COLUMNS)

    def test_absent_concept_is_null_not_zero(self) -> None:
        out = from_bond(bond_frame())
        self.assertTrue(out["expense_ratio"].isna().all())
        self.assertFalse(bool(out["expense_ratio_available"].iloc[0]))
        self.assertEqual(out["expense_ratio_source"].iloc[0], "not_applicable_for_product_type")

    def test_fund_expense_is_absent_in_source_not_zero(self) -> None:
        out = from_fund(fund_frame())
        self.assertTrue(out["expense_ratio"].isna().all())
        self.assertEqual(out["expense_ratio_source"].iloc[0], "column_absent_in_source")

    def test_etf_and_etn_get_separate_product_types(self) -> None:
        out = from_domestic_etp(domestic_frame())
        self.assertEqual(
            out["product_type"].tolist(),
            ["DOMESTIC_ETF", "DOMESTIC_ETN", "DOMESTIC_ETF"],
        )

    def test_ended_product_placeholder_return_is_dropped(self) -> None:
        out = from_domestic_etp(domestic_frame())
        self.assertTrue(pd.isna(out.loc[2, "return_1y"]))
        self.assertFalse(bool(out.loc[2, "return_1y_available"]))
        self.assertEqual(out.loc[0, "return_1y"], 5.5)

    def test_metric_as_of_differs_from_extract_date(self) -> None:
        out = from_overseas_etp(overseas_frame())
        self.assertEqual(out.loc[0, "extract_date"], "2026-07-11")
        self.assertEqual(out.loc[0, "expense_ratio_as_of"], "2026-06-14")
        self.assertEqual(out.loc[0, "aum_as_of"], "2026-06-14")

    def test_overseas_has_no_one_year_return(self) -> None:
        out = from_overseas_etp(overseas_frame())
        self.assertTrue(out["return_1y"].isna().all())
        self.assertEqual(out["return_1y_source"].iloc[0], "column_absent_in_source")

    def test_taxonomy_raw_is_preserved_with_unmapped_status(self) -> None:
        out = from_overseas_etp(overseas_frame())
        self.assertEqual(out.loc[0, "region_raw"], "United States of America")
        self.assertTrue(pd.isna(out.loc[0, "region_std"]))
        self.assertEqual(out.loc[0, "region_mapping_status"], "unmapped")

    def test_bond_taxonomy_is_not_applicable(self) -> None:
        out = from_bond(bond_frame())
        self.assertEqual(out.loc[0, "region_mapping_status"], "not_applicable")

    def test_available_flag_never_true_without_value(self) -> None:
        for adapter, frame in (
            (from_bond, bond_frame()),
            (from_domestic_etp, domestic_frame()),
            (from_overseas_etp, overseas_frame()),
            (from_fund, fund_frame()),
        ):
            out = adapter(frame)
            for metric in ("expense_ratio", "aum", "return_1y"):
                with self.subTest(adapter=adapter.__name__, metric=metric):
                    self.assertFalse(
                        bool((out[f"{metric}_available"] & out[metric].isna()).any())
                    )


if __name__ == "__main__":
    unittest.main()
