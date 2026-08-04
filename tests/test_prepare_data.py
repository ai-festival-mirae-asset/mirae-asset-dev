import unittest
from datetime import date, datetime, timezone

import pandas as pd

from pipeline.prepare_data import clean_text, date_token
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
