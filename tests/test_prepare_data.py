import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.load_dictionary import PRODUCT_GROUP_TO_CODE, read_dictionary

from pipeline.prepare_data import (
    CREDIT_GRADE_RANK,
    IssueLog,
    clean_text,
    credit_grade_rank,
    date_token,
    normalize_credit_grade,
    normalize_observed_flag,
)
from pipeline.time_policy import (
    maturity_status_as_of,
    remaining_days_as_of,
    resolve_as_of_date,
    service_today,
)


class NormalizationUnitTests(unittest.TestCase):
    def test_clean_text_trims_and_preserves_codes(self) -> None:
        self.assertEqual(clean_text(" 00020054 "), "00020054")
        self.assertEqual(clean_text(12), "12")
        self.assertIs(clean_text("   "), pd.NA)

    def test_date_token_accepts_excel_timestamp_text(self) -> None:
        self.assertEqual(date_token("2026-06-15 00:00:00"), "20260615")
        self.assertEqual(date_token("2026-06-15"), "20260615")
        self.assertIs(date_token(""), pd.NA)

    def test_date_token_preserves_sentinel_for_policy_layer(self) -> None:
        self.assertEqual(date_token("99991231"), "99991231")


class CreditGradeUnitTests(unittest.TestCase):
    def test_flat_notation_is_normalized(self) -> None:
        self.assertEqual(normalize_credit_grade("AA0"), "AA")
        self.assertEqual(normalize_credit_grade("BBB0"), "BBB")
        self.assertEqual(normalize_credit_grade(" A0 "), "A")

    def test_grades_without_flat_suffix_are_untouched(self) -> None:
        self.assertEqual(normalize_credit_grade("AAA"), "AAA")
        self.assertEqual(normalize_credit_grade("AA+"), "AA+")
        self.assertEqual(normalize_credit_grade("CCC"), "CCC")
        self.assertIs(normalize_credit_grade(""), pd.NA)

    def test_aa_or_better_includes_flat_notation(self) -> None:
        grades = pd.Series(["AAA", "AA+", "AA0", "AA-", "A+"]).map(normalize_credit_grade)
        ranks = credit_grade_rank(grades)
        self.assertEqual(int((ranks <= 3).sum()), 3)
        self.assertEqual(CREDIT_GRADE_RANK["AA"], 3)

    def test_rank_is_monotonic_from_aaa_to_d(self) -> None:
        ranks = list(CREDIT_GRADE_RANK.values())
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(CREDIT_GRADE_RANK["AAA"], 1)
        self.assertEqual(CREDIT_GRADE_RANK["D"], len(CREDIT_GRADE_RANK))


class ObservedFlagUnitTests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "flag": pd.Series(["Y", pd.NA, pd.NA], dtype="object"),
                "group": ["ETN", "ETF", "ETF"],
            }
        )

    def test_missing_is_filled_only_with_corroboration(self) -> None:
        frame = self._frame()
        issues = IssueLog()
        result = normalize_observed_flag(
            frame, "flag", "Y", "PREF02N001", issues, corroborating=frame["group"].eq("ETN")
        )
        self.assertEqual(result.tolist(), [True, False, False])
        self.assertTrue(issues.frame().empty)

    def test_missing_stays_unknown_without_corroboration(self) -> None:
        frame = self._frame()
        issues = IssueLog()
        result = normalize_observed_flag(frame, "flag", "Y", "PREF02N001", issues)
        self.assertTrue(result.iloc[0])
        self.assertTrue(result.isna().iloc[1:].all())
        self.assertEqual(
            issues.frame()["issue_code"].tolist(), ["flag_fill_needs_corroboration"]
        )

    def test_conflicting_corroboration_keeps_missing_unknown(self) -> None:
        frame = self._frame()
        issues = IssueLog()
        result = normalize_observed_flag(
            frame, "flag", "Y", "PREF02N001", issues, corroborating=frame["group"].eq("ETF")
        )
        self.assertTrue(result.isna().iloc[1:].all())
        self.assertFalse(issues.frame().empty)

    def test_negative_only_column_is_not_folded(self) -> None:
        frame = pd.DataFrame({"flag": pd.Series(["N", pd.NA], dtype="object")})
        issues = IssueLog()
        result = normalize_observed_flag(frame, "flag", "Y", "PREF02N001", issues)
        self.assertFalse(bool(result.iloc[0]))
        self.assertTrue(result.isna().iloc[1])
        self.assertEqual(
            issues.frame()["issue_code"].tolist(), ["flag_fill_rule_not_applicable"]
        )


class DictionaryLoaderUnitTests(unittest.TestCase):
    def test_bom_and_padding_are_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "사전.csv"
            path.write_text("﻿분류,키,한글명\n  등급 ,  AA0  ,무부호\n", encoding="utf-8")
            frame = read_dictionary(path)
            self.assertEqual(list(frame.columns), ["분류", "키", "한글명"])
            self.assertEqual(frame.loc[0, "키"], "AA0")
            self.assertEqual(frame.loc[0, "분류"], "등급")

    def test_product_group_maps_to_dataset_code(self) -> None:
        self.assertEqual(PRODUCT_GROUP_TO_CODE["국내채권"], "PRBD01N001")
        self.assertEqual(PRODUCT_GROUP_TO_CODE["해외ETF"], "PREF02N001")
        self.assertEqual(len(PRODUCT_GROUP_TO_CODE), 4)


class TimePolicyUnitTests(unittest.TestCase):
    def test_seoul_date_is_used_at_request_boundary(self) -> None:
        utc_clock = datetime(2026, 8, 3, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(service_today(utc_clock), date(2026, 8, 4))

    def test_explicit_as_of_date_has_priority(self) -> None:
        requested = date(2026, 7, 11)
        self.assertEqual(resolve_as_of_date(requested), requested)

    def test_remaining_days_is_calculated_for_each_request(self) -> None:
        maturity = date(2055, 5, 14)
        self.assertEqual(remaining_days_as_of(maturity, date(2026, 7, 11)), 10534)
        self.assertEqual(remaining_days_as_of(maturity, date(2026, 8, 4)), 10510)
        self.assertEqual(maturity_status_as_of(maturity, date(2055, 5, 14)), "matures_on_as_of_date")

    def test_unknown_maturity_remains_unknown(self) -> None:
        self.assertIsNone(remaining_days_as_of(None, date(2026, 8, 4)))
        self.assertEqual(maturity_status_as_of(None, date(2026, 8, 4)), "unknown")


if __name__ == "__main__":
    unittest.main()
