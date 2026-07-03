"""CLI/integration tests for the portfolio analysis subcommands (stdlib unittest).

Each test builds a SYNTHETIC lots DB via portfolio.load() from a synthetic CSV (no real financial
data), then drives a subcommand and asserts key output. Later feature nodes add their own classes.
"""
import contextlib
import datetime as dt
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "analyze"))
import portfolio  # noqa: E402

AS_OF = dt.date(2026, 7, 1)
HEADERS = ",".join(portfolio.EXPECTED_HEADERS)


def _row(account, symbol, qty, acquired, avg, cost, value, gl, glp, desc="", mc="Margin"):
    fields = [account, symbol, desc, mc, qty, acquired, "", "", avg, cost, value, gl, glp]
    return ",".join('"' + str(f).replace('"', '""') + '"' for f in fields)


def build_db(rows):
    fd, csvp = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(csvp, "w", encoding="utf-8") as fh:
        fh.write(HEADERS + "\n" + "\n".join(rows) + "\n")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    portfolio.load(csvp, db, AS_OF)
    os.unlink(csvp)
    return db


def run(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*a, **kw)
    return out.getvalue()


# A small synthetic portfolio reused across subcommand tests.
SAMPLE_ROWS = [
    _row("Individual - TOD Test", "LOSSA", 10, "Jan-05-2026", "$110.00", "$1,100.00", "$900.00", "-$200.00", "-18.18%"),
    _row("Individual - TOD Test", "LOSSB", 5, "Jan-05-2024", "$200.00", "$1,000.00", "$850.00", "-$150.00", "-15.00%"),
    _row("Individual - TOD Test", "GAINC", 4, "Feb-01-2026", "$50.00", "$200.00", "$260.00", "+$60.00", "+30.00%"),
    _row("Roth IRA Test", "LOSSD", 3, "Jan-01-2026", "$100.00", "$300.00", "$250.00", "-$50.00", "-16.67%"),
    _row("Individual - TOD Test", "CASH", "", "", "", "", "$500.00", "", "", desc="Cash HELD IN MONEY MARKET", mc=""),
]


class FetchLotsCliTests(unittest.TestCase):
    def test_readonly(self):
        db = build_db(SAMPLE_ROWS)
        try:
            self.assertEqual(len(portfolio.fetch_lots(db)), len(SAMPLE_ROWS))
            conn = portfolio.readonly_connection(db)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("UPDATE lots SET quantity=0")
            finally:
                conn.close()
        finally:
            os.unlink(db)


class HarvestCliTests(unittest.TestCase):
    def test_output(self):
        db = build_db(SAMPLE_ROWS)
        try:
            text = run(portfolio.cmd_harvest, db, AS_OF, 0.32, 0.15)
        finally:
            os.unlink(db)
        self.assertIn("LOSSA", text)
        self.assertIn("LOSSB", text)
        self.assertNotIn("GAINC", text)   # a gain is not harvestable
        self.assertNotIn("LOSSD", text)   # Roth IRA excluded
        self.assertLess(text.index("LOSSA"), text.index("LOSSB"))  # short-term first
        # estimated benefit = 200*0.32 + 150*0.15 = 86.50
        self.assertIn("86.50", text)
        self.assertIn("not tax advice", text)


class RipeningCliTests(unittest.TestCase):
    def test_output(self):
        db = build_db(SAMPLE_ROWS)
        try:
            text = run(portfolio.cmd_ripening, db, AS_OF, None, 0.32, 0.15)
        finally:
            os.unlink(db)
        self.assertIn("LOSSA", text)   # short-term loss -> ripening loser
        self.assertIn("GAINC", text)   # short-term gain -> ripening winner
        self.assertNotIn("LOSSB", text)  # long-term, excluded
        self.assertNotIn("LOSSD", text)  # Roth IRA excluded
        self.assertIn("HARVEST BEFORE RIPENING", text)


class ConcentrationCliTests(unittest.TestCase):
    def test_output(self):
        db = build_db(SAMPLE_ROWS)
        try:
            text = run(portfolio.cmd_concentration, db, 10, 0.05)
        finally:
            os.unlink(db)
        self.assertIn("LOSSA", text)              # all accounts included (cross-account)
        self.assertIn("Invested (non-cash)", text)
        self.assertIn("HHI", text)


SELL_ROWS = [
    _row("Individual - TOD Test", "MULTI", 10, "Jan-05-2026", "$12.00", "$120.00", "$100.00", "-$20.00", "-16.67%"),
    _row("Individual - TOD Test", "MULTI", 10, "Jun-15-2026", "$9.00", "$90.00", "$100.00", "+$10.00", "+11.11%"),
    _row("Individual - TOD Test", "MULTI", 10, "Jan-05-2024", "$8.00", "$80.00", "$100.00", "+$20.00", "+25.00%"),
]


class SellCliTests(unittest.TestCase):
    def test_output(self):
        db = build_db(SELL_ROWS)
        try:
            text = run(portfolio.cmd_sell, db, "MULTI", 15, None, "hifo", AS_OF, 0.32, 0.15)
        finally:
            os.unlink(db)
        self.assertIn("MULTI", text)
        self.assertIn("Individual - TOD Test", text)
        self.assertIn("vs FIFO", text)
        self.assertIn("not tax advice", text)


if __name__ == "__main__":
    unittest.main()
