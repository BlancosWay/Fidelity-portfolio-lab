"""Tests for scripts/analyze/common.py shared helpers + portfolio.fetch_lots (stdlib unittest).

Synthetic data only.
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "analyze"))
import common  # noqa: E402
import portfolio  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_lots.csv")
AS_OF = dt.date(2026, 7, 1)


class MoneyQtyTests(unittest.TestCase):
    def test_parse_money(self):
        self.assertAlmostEqual(common.parse_money("$1,425.00"), 1425.0)
        self.assertAlmostEqual(common.parse_money("-$900.00"), -900.0)
        self.assertAlmostEqual(common.parse_money("($5.00)"), -5.0)
        self.assertAlmostEqual(common.parse_money("+64.40%"), 64.40)
        for empty in (None, "", "   ", "-", "--", "n/a"):
            self.assertIsNone(common.parse_money(empty))

    def test_parse_qty(self):
        self.assertAlmostEqual(common.parse_qty("0.5"), 0.5)
        self.assertAlmostEqual(common.parse_qty("1,200"), 1200.0)
        self.assertEqual(common.parse_qty(None), 0.0)
        self.assertEqual(common.parse_qty("junk"), 0.0)


class DateTests(unittest.TestCase):
    def test_parse_date_positions(self):
        self.assertEqual(common.parse_date("Mar-11-2026"), dt.date(2026, 3, 11))
        self.assertEqual(common.parse_date("2026-03-11"), dt.date(2026, 3, 11))
        self.assertEqual(common.parse_date("03/11/2026"), dt.date(2026, 3, 11))
        self.assertIsNone(common.parse_date(""))
        self.assertIsNone(common.parse_date("not a date"))

    def test_parse_us_date_history(self):
        self.assertEqual(common.parse_us_date("07-02-2026"), dt.date(2026, 7, 2))
        self.assertEqual(common.parse_us_date("7-2-2026"), dt.date(2026, 7, 2))
        self.assertEqual(common.parse_us_date("07/02/2026"), dt.date(2026, 7, 2))
        # footer / junk / out-of-range -> None (so the loader skips those rows)
        for bad in ("", "   ", '"1038360.4.0"', "Date downloaded 07/03/2026 09:54 am", "13-40-2026"):
            self.assertIsNone(common.parse_us_date(bad))

    def test_one_year_anniversary(self):
        self.assertEqual(common.one_year_anniversary(dt.date(2024, 6, 18)), dt.date(2025, 6, 18))
        self.assertEqual(common.one_year_anniversary(dt.date(2024, 2, 29)), dt.date(2025, 2, 28))

    def test_holding_term(self):
        acq = dt.date(2024, 6, 18)
        self.assertEqual(common.holding_term(acq, dt.date(2025, 6, 18)), "Short-Term")  # exactly 1yr
        self.assertEqual(common.holding_term(acq, dt.date(2025, 6, 19)), "Long-Term")   # 1yr + 1 day
        leap = dt.date(2024, 2, 29)
        self.assertEqual(common.holding_term(leap, dt.date(2025, 2, 28)), "Short-Term")
        self.assertEqual(common.holding_term(leap, dt.date(2025, 3, 1)), "Long-Term")
        self.assertIsNone(common.holding_term(None, dt.date(2025, 1, 1)))


class ReExportTests(unittest.TestCase):
    def test_portfolio_reexports_common(self):
        # Existing callers use portfolio.<name>; those must still resolve to the common impls.
        for name in ("parse_money", "parse_qty", "parse_date", "one_year_anniversary", "holding_term", "MONTHS"):
            self.assertIs(getattr(portfolio, name), getattr(common, name))


class FetchLotsTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.n = portfolio.load(SAMPLE, self.db, AS_OF)

    def tearDown(self):
        os.unlink(self.db)

    def test_fetch_lots_returns_dicts(self):
        rows = portfolio.fetch_lots(self.db)
        self.assertEqual(len(rows), self.n)
        self.assertTrue(all(isinstance(r, dict) for r in rows))
        for key in ("account", "symbol", "quantity", "date_acquired", "term",
                    "cost_basis_total", "current_value", "gain_loss"):
            self.assertIn(key, rows[0])

    def test_fetch_lots_is_readonly(self):
        # fetch_lots must never permit a write; the underlying connection is mode=ro + query_only.
        conn = portfolio.readonly_connection(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE lots SET quantity=0")
        finally:
            conn.close()


class DeepDiveReproCommonTests(unittest.TestCase):
    """F9b: a parenthesized-negative quantity should parse to a negative number (like the exporter's
    num()), not silently collapse to 0.0."""

    def test_f9b_parse_qty_parenthesized_negative(self):
        self.assertEqual(common.parse_qty("(100)"), -100.0)
        self.assertEqual(common.parse_qty("(1,234.5)"), -1234.5)


if __name__ == "__main__":
    unittest.main()
