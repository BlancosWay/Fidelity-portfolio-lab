"""Tests for scripts/check_data_safety.py (stdlib unittest)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import check_data_safety as cds  # noqa: E402


class DataSafetyUnitTests(unittest.TestCase):
    def test_disallowed_paths(self):
        self.assertIsNotNone(cds.disallowed_path("data/fidelity_lots.csv"))
        self.assertIsNotNone(cds.disallowed_path("anywhere/holdings.tsv"))
        self.assertIsNotNone(cds.disallowed_path("data/portfolio.db"))
        self.assertIsNotNone(cds.disallowed_path("x.sqlite3"))
        self.assertIsNone(cds.disallowed_path("tests/sample_lots.csv"))
        self.assertIsNone(cds.disallowed_path("scripts/analyze/portfolio.py"))

    def test_disallowed_spreadsheet_exports(self):
        for p in ("data/Holdings.xlsx", "data/Holdings.XLSX", "export.xls", "book.xlsm",
                  "data/Holdings.xlsb", "data/Holdings.XLSB", "sheet.ods", "book.numbers"):
            self.assertIsNotNone(cds.disallowed_path(p), p)

    def test_scan_flags_fidelity_account_id(self):
        # A real Fidelity-style account number (Z + 8 digits) must be caught.
        self.assertTrue(cds.scan_text("README.md", "Account: Z05596750 balance"))

    def test_scan_flags_nine_digit_in_csv_only(self):
        self.assertTrue(cds.scan_text("data/x.csv", "Roth IRA 237378551,AAPL"))
        # A 9-digit number in prose/code is not treated as an account leak.
        self.assertFalse(cds.scan_text("scripts/foo.py", "id = 237378551"))

    def test_scan_clean_on_synthetic_sample_style(self):
        sample = "Individual - TOD Z111,AAPL,APPLE INC,Margin,50,Aug-20-2021"
        self.assertEqual(cds.scan_text("tests/sample_lots.csv", sample), [])


class DataSafetyRepoIntegration(unittest.TestCase):
    def test_repo_is_clean(self):
        files = cds.tracked_files()
        self.assertIn("tests/sample_lots.csv", files)
        self.assertEqual(cds.check(files, cds._read), [])


if __name__ == "__main__":
    unittest.main()
