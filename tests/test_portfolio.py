"""Tests for the portfolio analyzer (stdlib unittest)."""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "analyze"))
import portfolio  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_lots.csv")
AS_OF = dt.date(2026, 7, 1)


class TermTests(unittest.TestCase):
    def test_boundary_normal(self):
        acq = dt.date(2024, 6, 18)
        self.assertEqual(portfolio.holding_term(acq, dt.date(2025, 6, 18)), "Short-Term")  # exactly 1yr
        self.assertEqual(portfolio.holding_term(acq, dt.date(2025, 6, 19)), "Long-Term")   # 1yr + 1 day

    def test_boundary_leap(self):
        acq = dt.date(2024, 2, 29)
        self.assertEqual(portfolio.one_year_anniversary(acq), dt.date(2025, 2, 28))
        self.assertEqual(portfolio.holding_term(acq, dt.date(2025, 2, 28)), "Short-Term")
        self.assertEqual(portfolio.holding_term(acq, dt.date(2025, 3, 1)), "Long-Term")

    def test_parsers(self):
        self.assertEqual(portfolio.parse_date("Mar-11-2026"), dt.date(2026, 3, 11))
        self.assertEqual(portfolio.parse_date("2026-03-11"), dt.date(2026, 3, 11))
        self.assertAlmostEqual(portfolio.parse_money("$1,425.00"), 1425.0)
        self.assertAlmostEqual(portfolio.parse_money("-$900.00"), -900.0)
        self.assertAlmostEqual(portfolio.parse_money("($5.00)"), -5.0)
        self.assertAlmostEqual(portfolio.parse_money("+64.40%"), 64.40)
        self.assertAlmostEqual(portfolio.parse_qty("0.5"), 0.5)


class LoadTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.n = portfolio.load(SAMPLE, self.db, AS_OF)
        self.conn = sqlite3.connect(self.db)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db)

    def q(self, sql, *a):
        return self.conn.execute(sql, a).fetchall()

    def test_row_count(self):
        self.assertEqual(self.n, 11)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM lots")[0]["c"], 11)

    def test_units_per_symbol_across_accounts(self):
        r = self.q("SELECT ROUND(SUM(quantity),4) u, COUNT(DISTINCT account) a FROM lots WHERE symbol='AAPL'")[0]
        self.assertAlmostEqual(r["u"], 95.0)
        self.assertEqual(r["a"], 2)
        self.assertAlmostEqual(self.q("SELECT ROUND(SUM(quantity),4) u FROM lots WHERE symbol='BTC'")[0]["u"], 0.6)

    def test_long_short_units_for_symbol(self):
        r = self.q(
            """SELECT ROUND(SUM(CASE WHEN term='Long-Term' THEN quantity ELSE 0 END),4) lu,
                      ROUND(SUM(CASE WHEN term='Short-Term' THEN quantity ELSE 0 END),4) su
               FROM lots WHERE symbol='AAPL'""")[0]
        self.assertAlmostEqual(r["lu"], 80.0)
        self.assertAlmostEqual(r["su"], 15.0)

    def test_long_short_counts(self):
        counts = {row["term"]: row["c"] for row in self.q("SELECT term, COUNT(*) c FROM lots GROUP BY term")}
        self.assertEqual(counts["Long-Term"], 6)
        self.assertEqual(counts["Short-Term"], 5)

    def test_boundary_rows_from_csv(self):
        terms = {row["quantity"]: row["term"] for row in self.q("SELECT quantity, term FROM lots WHERE symbol='GRAB'")}
        self.assertEqual(terms[100.0], "Short-Term")  # acquired exactly 1 year before AS_OF
        self.assertEqual(terms[50.0], "Long-Term")    # acquired 1 year + 1 day before AS_OF

    def test_per_account_counts(self):
        counts = {row["account"]: row["c"] for row in self.q("SELECT account, COUNT(*) c FROM lots GROUP BY account")}
        self.assertEqual(counts["Individual - TOD Z111"], 8)
        self.assertEqual(counts["Roth IRA 222"], 3)

    def test_term_preview_column_not_trusted(self):
        # The CSV also carries "Term (Fidelity)"; the analyzer stores its own recomputed `term`.
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(lots)")}
        self.assertIn("term", cols)
        self.assertIn("term_fidelity", cols)


class QueryGuardTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        portfolio.load(SAMPLE, self.db, AS_OF)

    def tearDown(self):
        os.unlink(self.db)

    def test_select_allowed(self):
        self.assertEqual(portfolio.run_query(self.db, "SELECT COUNT(*) AS c FROM lots")[0]["c"], 11)

    def test_with_cte_allowed(self):
        rows = portfolio.run_query(self.db, "WITH x AS (SELECT quantity FROM lots) SELECT COUNT(*) c FROM x")
        self.assertEqual(rows[0]["c"], 11)

    def test_rejects_dangerous(self):
        for bad in [
            "UPDATE lots SET quantity=0",
            "DELETE FROM lots",
            "DROP TABLE lots",
            "ATTACH DATABASE 'x.db' AS y",
            "PRAGMA table_info(lots)",
            "SELECT 1; DROP TABLE lots",
            "INSERT INTO lots DEFAULT VALUES",
            "",
        ]:
            with self.assertRaises(ValueError, msg=f"should reject: {bad!r}"):
                portfolio.run_query(self.db, bad)

    def test_engine_is_readonly(self):
        # Even if validation were bypassed, the connection itself rejects writes.
        conn = portfolio.readonly_connection(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE lots SET quantity=0")
        finally:
            conn.close()


class HeaderContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = []

    def tearDown(self):
        for p in self._tmp:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _write(self, header_line):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self._tmp.append(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(header_line + "\n")
        return path

    def test_default_db_is_repo_data_dir(self):
        # DEFAULT_DB must resolve to <repo>/data/portfolio.db (one level above scripts/), not
        # scripts/data/. Derive the expectation from portfolio.py's own location (name-agnostic).
        analyze_dir = os.path.dirname(os.path.abspath(portfolio.__file__))  # <repo>/scripts/analyze
        expected = os.path.normpath(os.path.join(analyze_dir, "..", "..", "data", "portfolio.db"))
        self.assertEqual(os.path.normpath(portfolio.DEFAULT_DB), expected)

    def test_header_tolerance_and_missing_required(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._tmp.append(db)
        # Reordered + extra columns are tolerated (mapped by name) -- header-only file loads 0 rows.
        reordered = "Symbol,Account," + ",".join(portfolio.EXPECTED_HEADERS[2:]) + ",Percent Of Account"
        self.assertEqual(portfolio.load(self._write(reordered), db, AS_OF), 0)
        # A MISSING required column is still fatal.
        dropped = ",".join(h for h in portfolio.EXPECTED_HEADERS if h != "Quantity")
        with self.assertRaises(ValueError):
            portfolio.load(self._write(dropped), db, AS_OF)
        # A DUPLICATED required column is ambiguous (csv.DictReader keeps the last) -> reject.
        duped = ",".join(portfolio.EXPECTED_HEADERS) + ",Symbol"
        with self.assertRaises(ValueError):
            portfolio.load(self._write(duped), db, AS_OF)


class DeepDiveReproPortfolioTests(unittest.TestCase):
    """F5 (load tolerates benign header drift) and F9c (query keyword inside a string literal)."""

    def setUp(self):
        self._tmp = []

    def tearDown(self):
        for p in self._tmp:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _csv(self, header_cols, row_vals):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self._tmp.append(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(",".join(header_cols) + "\n")
            fh.write(",".join('"' + str(v) + '"' for v in row_vals) + "\n")
        return path

    def _db(self):
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._tmp.append(db)
        return db

    def test_f5_load_tolerates_extra_and_reordered_columns(self):
        # Benign header drift must NOT brick the load: an extra trailing column AND reordered columns
        # (values mapped by header name, not position).
        row = {h: v for h, v in zip(portfolio.EXPECTED_HEADERS,
                                    ["Ind", "AAPL", "", "Margin", "10", "Jan-05-2026", "", "",
                                     "$100", "$1000", "$1100", "+$100", "+10%"])}
        reordered = list(portfolio.EXPECTED_HEADERS)[2:] + list(portfolio.EXPECTED_HEADERS)[:2]
        cols = reordered + ["Percent Of Account"]
        vals = [row[h] for h in reordered] + ["5%"]
        db = self._db()
        n = portfolio.load(self._csv(cols, vals), db, AS_OF)
        self.assertEqual(n, 1)
        got = portfolio.fetch_lots(db)[0]
        self.assertEqual(got["account"], "Ind")     # mapped by name despite the reorder
        self.assertEqual(got["symbol"], "AAPL")

    def test_f9c_query_allows_keyword_inside_string_literal(self):
        # A disallowed keyword appearing only inside a quoted string literal is data, not a statement.
        stmt = portfolio._validate_query("SELECT * FROM lots WHERE symbol='CREATE'")
        self.assertIn("CREATE", stmt)
        stmt2 = portfolio._validate_query("SELECT * FROM lots WHERE description LIKE '%replace%'")
        self.assertIn("replace", stmt2)


if __name__ == "__main__":
    unittest.main()
