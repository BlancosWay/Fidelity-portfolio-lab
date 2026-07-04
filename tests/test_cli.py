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
        # No offsetting gains: the -350 net loss (200 ST + 150 LT) deducts against ordinary income
        # (under the $3k cap) at the ordinary rate -> 350 * 0.32 = 112.00.
        self.assertIn("112.00", text)
        self.assertIn("not tax advice", text)

    def test_offsetting_lt_gains_uses_lt_rate(self):
        db = build_db(SAMPLE_ROWS)
        try:                                            # 150 LT loss offsets an LT gain -> lt_rate
            text = run(portfolio.cmd_harvest, db, AS_OF, 0.32, 0.15, 0.0, 100000.0)
        finally:
            os.unlink(db)
        # ST loss 200 offsets LT gain at lt_rate too here (net gain path); benefit = 350*0.15 = 52.50
        self.assertIn("52.50", text)
        self.assertIn("not tax advice", text)

    def test_price_dispersion_warning_case_insensitive(self):
        # A lowercase-symbol loss lot with inconsistent per-share prices must still warn.
        db = build_db([
            _row("Individual - TOD Test", "disp", 10, "Jan-05-2024", "$100.00", "$1,000.00", "$100.00", "-$900.00", "-90.00%"),
            _row("Individual - TOD Test", "disp", 5, "Jan-05-2024", "$100.00", "$500.00", "$500.00", "$0.00", "0.00%"),
        ])
        try:
            text = run(portfolio.cmd_harvest, db, AS_OF, 0.32, 0.15)
        finally:
            os.unlink(db)
        self.assertIn("inconsistent per-share prices", text)


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

    def test_excludes_tax_advantaged_and_flags_multi_account(self):
        db = build_db([
            _row("Individual - TOD Test", "SHR", 10, "Jan-05-2024", "$8.00", "$80.00", "$100.00", "+$20.00", "+25.00%"),
            _row("Joint Brokerage Test", "SHR", 10, "Jan-05-2024", "$8.00", "$80.00", "$100.00", "+$20.00", "+25.00%"),
            _row("Roth IRA Test", "SHR", 10, "Jan-05-2024", "$8.00", "$80.00", "$100.00", "+$20.00", "+25.00%"),
        ])
        try:
            text = run(portfolio.cmd_sell, db, "SHR", 20, None, "fifo", AS_OF, 0.32, 0.15)
        finally:
            os.unlink(db)
        self.assertNotIn("Roth IRA Test", text)                 # tax-advantaged lot excluded
        self.assertIn("per-account", text)                      # multi-account NOTE
        self.assertIn("not tax advice", text)

    def test_sell_help_does_not_crash(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            portfolio.main(["sell", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_price_dispersion_warning(self):
        # Same symbol, inconsistent per-share (scrape corruption): $200/sh vs $10/sh.
        db = build_db([
            _row("Individual - TOD Test", "DISP", 10, "Jan-05-2024", "$100.00", "$1,000.00", "$2,000.00", "+$1,000.00", "+100.00%"),
            _row("Individual - TOD Test", "DISP", 10, "Jan-05-2024", "$100.00", "$1,000.00", "$100.00", "-$900.00", "-90.00%"),
        ])
        try:
            text = run(portfolio.cmd_sell, db, "DISP", 10, None, "loss-first", AS_OF, 0.32, 0.15)
        finally:
            os.unlink(db)
        self.assertIn("inconsistent per-share prices", text)


HIST_HEADER = ("Run Date,Account,Account Number,Action,Symbol,Description,Type,Exchange Quantity,"
               "Exchange Currency,Currency,Price,Quantity,Exchange Rate,Commission,Fees,"
               "Accrued Interest,Amount,Settlement Date")


def build_history(rows):
    fd, p = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(HIST_HEADER + "\n" + "\n".join(rows) + "\n")
    return p


class WashSaleCliTests(unittest.TestCase):
    def test_blocked_output(self):
        db = build_db([
            _row("Individual - TOD Test", "AAA", 10, "Jun-15-2026", "$11.00", "$110.00", "$100.00", "-$10.00", "-9.09%"),
        ])
        hp = build_history([
            '06-20-2026,Roth IRA Test,333,YOU BOUGHT AAA CO (AAA) (Cash),AAA,AAA CO,Cash,0,,USD,10.00,10,0,"","","",-100,06-22-2026',
        ])
        try:
            text = run(portfolio.cmd_washsale, db, hp, AS_OF, 30, False)
        finally:
            os.unlink(db)
            os.unlink(hp)
        self.assertIn("BLOCKED", text)   # replacement buy in a Roth IRA -> permanent disallowance
        self.assertIn("AAA", text)
        self.assertIn("not tax advice", text)

    def test_review_output_brokeragelink(self):
        # A replacement buy in a 401(k)/BrokerageLink has no IRS wash-sale guidance -> REVIEW, not BLOCKED.
        db = build_db([
            _row("Individual - TOD Test", "AAA", 10, "Jun-15-2026", "$11.00", "$110.00", "$100.00", "-$10.00", "-9.09%"),
        ])
        hp = build_history([
            '06-20-2026,BrokerageLink Test,333,YOU BOUGHT AAA CO (AAA) (Cash),AAA,AAA CO,Cash,0,,USD,10.00,10,0,"","","",-100,06-22-2026',
        ])
        try:
            text = run(portfolio.cmd_washsale, db, hp, AS_OF, 30, False)
        finally:
            os.unlink(db)
            os.unlink(hp)
        self.assertRegex(text, r"REVIEW[^\n]*: 1")    # counted as REVIEW
        self.assertRegex(text, r"BLOCKED[^\n]*: 0")   # NOT blocked
        self.assertIn("AAA", text)

    def test_option_buy_to_open_same_underlying(self):
        db = build_db([
            _row("Individual - TOD Test", "AAA", 10, "Jun-15-2026", "$11.00", "$110.00", "$100.00", "-$10.00", "-9.09%"),
        ])
        hp = build_history([
            '06-20-2026,Individual - TOD Test,222,YOU BOUGHT OPENING TRANSACTION CALL (AAA) AAA JAN 15 27 $30 (100 SHS) (Cash), -AAA270115C30,CALL (AAA),Cash,0,,USD,1.00,2,0,"","","",-200,06-22-2026',
        ])
        try:
            clean = run(portfolio.cmd_washsale, db, hp, AS_OF, 30, False)          # exact-match only
            caution = run(portfolio.cmd_washsale, db, hp, AS_OF, 30, True)         # --same-underlying
        finally:
            os.unlink(db)
            os.unlink(hp)
        self.assertIn("CLEAN: 1", clean)      # a call is not the same security by default
        self.assertIn("CAUTION (replacement buy in a taxable account): 1", caution)


class CapacityCliTests(unittest.TestCase):
    LT_GAIN = _row("Individual - TOD Test", "GAINX", 100, "Jan-05-2024",
                   "$20.00", "$2,000.00", "$10,000.00", "+$8,000.00", "+400.00%")

    def test_headroom_output(self):
        db = build_db([self.LT_GAIN])
        try:
            text = run(portfolio.cmd_capacity, db, 40000.0, 50000.0, "0% LTCG", None, None, AS_OF, 0.15, 0.0)
        finally:
            os.unlink(db)
        self.assertIn("headroom", text)
        self.assertIn("GAINX", text)
        self.assertIn("not tax advice", text)

    def test_target_gain_output(self):
        db = build_db([self.LT_GAIN])
        try:
            text = run(portfolio.cmd_capacity, db, None, None, "0% LTCG", 5000.0, None, AS_OF, 0.15, 0.0)
        finally:
            os.unlink(db)
        self.assertIn("Target realized gain", text)

    def test_help_does_not_crash(self):
        # argparse %-formats help text, so a bare % in any help string raises ValueError at --help time.
        for argv in (["--help"], ["capacity", "--help"]):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                portfolio.main(argv)
            self.assertEqual(cm.exception.code, 0)


class GiftCliTests(unittest.TestCase):
    def test_output(self):
        db = build_db([
            _row("Individual - TOD Test", "DONX", 10, "Jan-05-2024",
                 "$100.00", "$1,000.00", "$4,000.00", "+$3,000.00", "+300.00%"),
        ])
        try:
            text = run(portfolio.cmd_gift, db, 0.0, 20, None, AS_OF, 0.15)
        finally:
            os.unlink(db)
        self.assertIn("DONX", text)
        self.assertIn("Donation candidates", text)
        self.assertIn("not tax advice", text)

    def test_gift_help_does_not_crash(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            portfolio.main(["gift", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_no_candidates_still_steers(self):
        # Only a short-term gain lot and a long-term loss lot -> no donation candidates, but the
        # steering counts must still print (they are the whole point of the anti-buckets).
        db = build_db([
            _row("Individual - TOD Test", "STG", 10, "Jun-01-2026",
                 "$100.00", "$1,000.00", "$1,300.00", "+$300.00", "+30.00%"),
            _row("Individual - TOD Test", "LTL", 10, "Jan-05-2024",
                 "$200.00", "$2,000.00", "$1,500.00", "-$500.00", "-25.00%"),
        ])
        try:
            text = run(portfolio.cmd_gift, db, 0.0, 20, None, AS_OF, 0.15)
        finally:
            os.unlink(db)
        self.assertIn("No taxable long-term appreciated lots", text)
        self.assertIn("1 short-term gain lot(s)", text)
        self.assertIn("1 loss lot(s)", text)
        self.assertIn("not tax advice", text)


class DashboardCliTests(unittest.TestCase):
    ROWS = [
        _row("Individual - TOD Test", "WIN", 10, "Jan-05-2024",
             "$100.00", "$1,000.00", "$3,000.00", "+$2,000.00", "+200.00%"),
        _row("Individual - TOD Test", "STL", 10, "Jun-01-2026",
             "$100.00", "$1,000.00", "$800.00", "-$200.00", "-20.00%"),
        _row("Individual - TOD Test", "CASH", "", "", "", "", "$500.00", "", "",
             desc="Cash HELD IN MONEY MARKET", mc=""),
    ]

    def test_output(self):
        db = build_db(self.ROWS)
        try:
            text = run(portfolio.cmd_dashboard, db, AS_OF, 0.32, 0.15, 60, None, None)
        finally:
            os.unlink(db)
        for token in ("Year-end tax dashboard", "Harvestable", "Ripening", "If sold now", "not tax advice"):
            self.assertIn(token, text)

    def test_output_with_capacity(self):
        db = build_db(self.ROWS)
        try:
            text = run(portfolio.cmd_dashboard, db, AS_OF, 0.32, 0.15, 60, 40000.0, 50000.0)
        finally:
            os.unlink(db)
        self.assertIn("Headroom", text)

    def test_dashboard_help_does_not_crash(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            portfolio.main(["dashboard", "--help"])
        self.assertEqual(cm.exception.code, 0)


class OptionsCliTests(unittest.TestCase):
    ROWS = [
        _row("Individual - TOD Test", "AAL", 100, "Jan-05-2024",
             "$13.00", "$1,300.00", "$1,300.00", "$0.00", "0.00%"),                      # stock -> spot 13
        _row("Individual - TOD Test", "AAL 17 Call", 5, "Mar-01-2026",
             "$1.60", "$800.00", "$1,000.00", "+$200.00", "+25.00%", desc="Jul-17-2026"),  # option
    ]

    def test_output(self):
        db = build_db(self.ROWS)
        try:
            text = run(portfolio.cmd_options, db, AS_OF, None, 20)
        finally:
            os.unlink(db)
        self.assertIn("AAL", text)
        self.assertIn("Notional", text)
        self.assertIn("not investment advice", text)

    def test_options_help_does_not_crash(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            portfolio.main(["options", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_expired_excluded_note(self):
        db = build_db([
            _row("Individual - TOD Test", "AAL", 100, "Jan-05-2024",
                 "$13.00", "$1,300.00", "$1,300.00", "$0.00", "0.00%"),
            _row("Individual - TOD Test", "AAL 10 Call", 2, "Mar-01-2026",
                 "$3.00", "$500.00", "$600.00", "+$100.00", "+20.00%", desc="Jul-17-2026"),   # live
            _row("Individual - TOD Test", "XYZ 100 Call", 3, "Mar-01-2025",
                 "$1.00", "$300.00", "$300.00", "$0.00", "0.00%", desc="Jan-16-2026"),         # expired
        ])
        try:
            text = run(portfolio.cmd_options, db, AS_OF, None, 20)
        finally:
            os.unlink(db)
        self.assertIn("expired option lot(s) excluded", text)
        self.assertNotIn("XYZ", text)


class ExpirationCliTests(unittest.TestCase):
    ROWS = [
        _row("Individual - TOD Test", "AAL", 100, "Jan-05-2024",
             "$13.00", "$1,300.00", "$1,300.00", "$0.00", "0.00%"),
        _row("Individual - TOD Test", "AAL 17 Call", 5, "Mar-01-2026",
             "$1.60", "$800.00", "$1,000.00", "+$200.00", "+25.00%", desc="Jul-17-2026"),
    ]

    def test_output(self):
        db = build_db(self.ROWS)
        try:
            text = run(portfolio.cmd_expiration, db, AS_OF, None, None, 30)
        finally:
            os.unlink(db)
        self.assertIn("AAL", text)
        self.assertIn("nearest", text)
        self.assertIn("not investment advice", text)

    def test_within_respected(self):
        db = build_db(self.ROWS)
        try:                                        # AAL 17 Call expires Jul-17 (16d from AS_OF) -> outside 5d
            text = run(portfolio.cmd_expiration, db, AS_OF, 5, None, 30)
        finally:
            os.unlink(db)
        self.assertIn("No dated option positions within 5 days", text)

    def test_expiration_help_does_not_crash(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            portfolio.main(["expiration", "--help"])
        self.assertEqual(cm.exception.code, 0)


class Tier2ReproCliTests(unittest.TestCase):
    """Reproduce Bug 8 (read-only + friendly errors) and Bug 5 at the report level. Currently FAIL."""

    def _missing_db_path(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)   # a path that does NOT exist
        return p

    def test_bug5_summary_recomputes_term_as_of(self):
        # A lot stored Short-Term (loaded as-of 2026-07-01) must display Long-Term when summary is run
        # with a later --as-of. The current summary(db) has no as-of and shows the stale stored term.
        db = build_db([
            _row("Individual - TOD Test", "AAPL", 10, "Jun-01-2026",
                 "$100.00", "$1,000.00", "$2,000.00", "+$1,000.00", "+100.00%"),   # stored Short-Term
        ])
        try:
            text = run(portfolio.summary, db, dt.date(2027, 8, 1))   # >1yr later -> Long-Term
        finally:
            os.unlink(db)
        self.assertIn("Long-Term", text)        # recomputed to long as of 2027
        self.assertNotIn("Short-Term", text)    # no longer bucketed short

    def test_bug8_reports_do_not_create_db(self):
        # summary/symbol/accounts must be strictly read-only: a missing DB must not be created.
        p = self._missing_db_path()
        try:
            run(portfolio.accounts_list, p)
        except Exception:
            pass   # a raise is acceptable here; the bug is the 0-byte FILE creation
        created = os.path.exists(p)
        try:
            os.unlink(p)   # best-effort; a leaked read-write handle may keep it locked on Windows
        except OSError:
            pass
        self.assertFalse(created, "accounts_list must not create the DB file")

    def test_bug8_missing_db_friendly_message(self):
        # An analysis command on a never-loaded DB must print a friendly hint, not raise a traceback.
        p = self._missing_db_path()
        try:
            text = run(portfolio.cmd_harvest, p, AS_OF, 0.32, 0.15)
        finally:
            if os.path.exists(p):
                os.unlink(p)
        self.assertIn("load", text.lower())


class DbReadonlyGuardTests(unittest.TestCase):
    """Node db-readonly-guard (Bug 8): every consumer must guard a missing/unloaded DB with a
    friendly hint and never create a file. Extends the two carried Tier2ReproCliTests repros to
    the remaining converted commands and the main() query branch."""

    def _missing_db_path(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)   # a path that does NOT exist
        return p

    def test_query_missing_db_returns_1_and_creates_no_file(self):
        p = self._missing_db_path()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = portfolio.main(["--db", p, "query", "SELECT 1 AS x"])
            self.assertEqual(rc, 1)                       # non-zero exit on missing DB
            self.assertIn("load", out.getvalue().lower())  # friendly hint, not a traceback
            self.assertFalse(os.path.exists(p), "query must not create the DB file")
        finally:
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def test_converted_commands_hint_on_missing_db(self):
        # Each analysis command converted to read_lots must print a load hint and not raise.
        cases = [
            (portfolio.cmd_options, (AS_OF, None, 10)),
            (portfolio.cmd_expiration, (AS_OF, 60, None, 10)),
            (portfolio.cmd_capacity, (100000.0, None, "0% LTCG", None, None, AS_OF, 0.15, 0.0)),
            (portfolio.cmd_gift, (0.0, 10, None, AS_OF, 0.15)),
            (portfolio.cmd_dashboard, (AS_OF, 0.32, 0.15, 60, 100000.0, None)),
        ]
        for fn, extra in cases:
            p = self._missing_db_path()
            try:
                text = run(fn, p, *extra)
                self.assertIn("load", text.lower(), f"{fn.__name__} should print a load hint")
                self.assertFalse(os.path.exists(p), f"{fn.__name__} must not create the DB file")
            finally:
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    def test_loaded_then_deleted_db_hint(self):
        # A DB that was loaded once but later removed must degrade to the friendly hint, not a crash.
        db = build_db(SAMPLE_ROWS)
        os.unlink(db)
        text = run(portfolio.cmd_dashboard, db, AS_OF, 0.32, 0.15, 60, 100000.0, None)
        self.assertIn("load", text.lower())
        if os.path.exists(db):
            try:
                os.unlink(db)
            except OSError:
                pass

    def test_query_bad_column_surfaces_real_error_not_load_hint(self):
        # A genuine SQL error on a LOADED DB must surface the real sqlite error, not the misleading
        # "No portfolio loaded" hint (regression: run_query previously swallowed all OperationalErrors).
        db = build_db(SAMPLE_ROWS)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = portfolio.main(["--db", db, "query", "SELECT no_such_column FROM lots"])
            text = out.getvalue().lower()
            self.assertEqual(rc, 1)                              # bad query still exits non-zero
            self.assertNotIn("no portfolio loaded", text)        # NOT the unloaded-DB hint
            self.assertIn("no such column", text)                # the actual sqlite error is shown
        finally:
            os.unlink(db)

    def test_query_other_missing_table_is_real_error_not_load_hint(self):
        # Exact-match guard: a missing table whose name merely starts with 'lots' (e.g. 'lots2') on a
        # LOADED DB is a genuine query error, not the unloaded-portfolio case.
        db = build_db(SAMPLE_ROWS)
        try:
            for q in ("SELECT * FROM lots2", "SELECT * FROM othertable"):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = portfolio.main(["--db", db, "query", q])
                text = out.getvalue().lower()
                self.assertEqual(rc, 1, q)
                self.assertNotIn("no portfolio loaded", text, q)   # not the load hint
                self.assertIn("no such table", text, q)            # the actual sqlite error
        finally:
            os.unlink(db)

    def test_query_missing_lots_table_shows_load_hint(self):
        # A DB file that exists but was never loaded (no 'lots' table) must map to the load hint.
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(db)   # create an empty DB with NO lots table
        conn.close()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = portfolio.main(["--db", db, "query", "SELECT * FROM lots"])
            text = out.getvalue().lower()
            self.assertEqual(rc, 1)
            self.assertIn("no portfolio loaded", text)
        finally:
            os.unlink(db)


if __name__ == "__main__":
    unittest.main()
