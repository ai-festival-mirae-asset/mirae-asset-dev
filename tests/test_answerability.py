import unittest

import pandas as pd

from pipeline.answerability import (
    ANSWERABLE,
    NO_MATCHING_ROWS,
    PARTIAL_COVERAGE,
    UNSUPPORTED_FIELD,
    assess,
    comparable_rows,
    policy_column,
    rank,
)


def policy_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "dataset": "PREF01N001",
                "column": "cu_charge_rt",
                "status": "partial_needs_review",
                "query_action": "positive_observed_only",
                "reason": "비결측 217개 중 150개가 0이라 양수만 사용합니다.",
            },
            {
                "dataset": "PREF01N001",
                "column": "pd_net_tamt",
                "status": "partial",
                "query_action": "compare_observed_only",
                "reason": "양수 관측값만 집계합니다.",
            },
            {
                "dataset": "PREF01N001",
                "column": "du_chas_errt",
                "status": "unavailable",
                "query_action": "reject_query",
                "reason": "비결측값이 모두 0입니다.",
            },
        ]
    )


def etp_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pd_itm_no": ["A", "B", "C", "D"],
            "expense_ratio": [0.15, 0.20, None, None],
            "expense_ratio_available": [True, True, False, False],
            "aum": [100.0, None, 300.0, None],
            "aum_available": [True, False, True, False],
            "du_chas_errt": [0.0, 0.0, 0.0, 0.0],
        }
    )


class AnswerabilityUnitTests(unittest.TestCase):
    def test_blocked_column_is_unsupported_not_missing_product(self) -> None:
        result = assess(etp_frame(), "PREF01N001", ["du_chas_errt"], policy_frame())
        self.assertEqual(result.status, UNSUPPORTED_FIELD)
        self.assertIn("du_chas_errt", result.blocked_columns)
        self.assertIn("상품이 없다는 뜻이 아닙니다", result.reason)

    def test_unknown_column_is_unsupported(self) -> None:
        result = assess(etp_frame(), "PREF01N001", ["cu_base_index"], policy_frame())
        self.assertEqual(result.status, UNSUPPORTED_FIELD)
        self.assertIn("cu_base_index", result.blocked_columns)

    def test_empty_eligible_set_is_no_matching_rows(self) -> None:
        empty = etp_frame().iloc[0:0]
        result = assess(empty, "PREF01N001", ["expense_ratio"], policy_frame())
        self.assertEqual(result.status, NO_MATCHING_ROWS)
        self.assertEqual(result.eligible_count, 0)

    def test_available_count_is_intersection_not_column_minimum(self) -> None:
        result = assess(etp_frame(), "PREF01N001", ["expense_ratio", "aum"], policy_frame())
        # 컬럼별로는 각각 2건이지만 두 값을 동시에 가진 행은 A 하나뿐이다.
        self.assertEqual(result.coverage_by_column["expense_ratio"], 0.5)
        self.assertEqual(result.coverage_by_column["aum"], 0.5)
        self.assertEqual(result.status, PARTIAL_COVERAGE)
        self.assertEqual(result.available_count, 1)
        self.assertEqual(result.excluded_count, 3)

    def test_full_coverage_is_answerable(self) -> None:
        frame = etp_frame().iloc[[0]]
        result = assess(frame, "PREF01N001", ["expense_ratio", "aum"], policy_frame())
        self.assertEqual(result.status, ANSWERABLE)
        self.assertEqual(result.available_count, 1)
        self.assertEqual(result.excluded_count, 0)

    def test_zero_available_rows_reports_data_absence(self) -> None:
        frame = etp_frame().iloc[[3]]
        result = assess(frame, "PREF01N001", ["expense_ratio"], policy_frame())
        self.assertEqual(result.status, UNSUPPORTED_FIELD)
        self.assertIn("데이터가 없는 것입니다", result.reason)

    def test_policy_action_becomes_user_facing_warning(self) -> None:
        result = assess(etp_frame(), "PREF01N001", ["expense_ratio"], policy_frame())
        self.assertTrue(any("양수" in warning for warning in result.warnings))

    def test_eligible_mask_restricts_population(self) -> None:
        frame = etp_frame()
        mask = pd.Series([True, False, False, False], index=frame.index)
        result = assess(
            frame, "PREF01N001", ["expense_ratio"], policy_frame(), eligible_mask=mask
        )
        self.assertEqual(result.eligible_count, 1)
        self.assertEqual(result.status, ANSWERABLE)


    def test_derived_column_resolves_to_source_policy(self) -> None:
        # 정책은 원본 컬럼명(cu_charge_rt)으로 기록돼 있고 질의는 파생명(expense_ratio)을 쓴다.
        self.assertEqual(policy_column("PREF01N001", "expense_ratio"), "cu_charge_rt")
        self.assertEqual(policy_column("PREF02N001", "aum"), "du_last_aum")
        self.assertEqual(policy_column("PREF01N001", "aum"), "pd_net_tamt")
        # 별칭이 없는 컬럼은 이름 그대로 조회한다.
        self.assertEqual(policy_column("PRFD01N001", "risk_grade"), "risk_grade")

    def test_blocked_policy_applies_through_alias(self) -> None:
        policy = policy_frame()
        policy.loc[policy["column"].eq("cu_charge_rt"), ["status", "query_action"]] = [
            "unavailable",
            "reject_query",
        ]
        result = assess(etp_frame(), "PREF01N001", ["expense_ratio"], policy)
        self.assertEqual(result.status, UNSUPPORTED_FIELD)
        self.assertIn("expense_ratio", result.blocked_columns)

    def test_min_coverage_downgrades_partial_to_unsupported(self) -> None:
        result = assess(
            etp_frame(), "PREF01N001", ["expense_ratio", "aum"], policy_frame(), min_coverage=0.5
        )
        self.assertEqual(result.status, UNSUPPORTED_FIELD)
        self.assertIn("커버리지가 부족", result.reason)

    def test_min_coverage_none_keeps_partial(self) -> None:
        result = assess(etp_frame(), "PREF01N001", ["expense_ratio", "aum"], policy_frame())
        self.assertEqual(result.status, PARTIAL_COVERAGE)


class RankUnitTests(unittest.TestCase):
    def test_rank_drops_rows_missing_required_values(self) -> None:
        ranked = rank(etp_frame(), [("expense_ratio", "asc"), ("aum", "desc")])
        self.assertEqual(ranked["pd_itm_no"].tolist(), ["A"])

    def test_rank_respects_limit_and_order(self) -> None:
        frame = pd.DataFrame(
            {
                "pd_itm_no": ["A", "B", "C"],
                "expense_ratio": [0.3, 0.1, 0.2],
                "expense_ratio_available": [True, True, True],
            }
        )
        ranked = rank(frame, [("expense_ratio", "asc")], limit=2)
        self.assertEqual(ranked["pd_itm_no"].tolist(), ["B", "C"])

    def test_comparable_rows_requires_every_column(self) -> None:
        mask = comparable_rows(etp_frame(), ["expense_ratio", "aum"])
        self.assertEqual(mask.tolist(), [True, False, False, False])


if __name__ == "__main__":
    unittest.main()
