"""Tests for scripts/analyze/history.py (stdlib unittest). Synthetic data only."""
import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "analyze"))
import history  # noqa: E402

HEADER = ("Run Date,Account,Account Number,Action,Symbol,Description,Type,Exchange Quantity,"
          "Exchange Currency,Currency,Price,Quantity,Exchange Rate,Commission,Fees,"
          "Accrued Interest,Amount,Settlement Date")

# Synthetic transactions (fake tickers/accounts/numbers) + realistic footer rows.
ROWS = [
    '06-10-2026,BrokerageLink Test,111111111,YOU BOUGHT FAKE CO (FAKE) (Cash),FAKE,FAKE CO,Cash,0,,USD,10.00,100,0,"","","",-1000,06-12-2026',
    '06-15-2026,Individual - TOD Test,222222222,YOU SOLD FAKE CO (FAKE) (Cash),FAKE,FAKE CO,Cash,0,,USD,9.00,-40,0,"",0.02,"",359.98,06-17-2026',
    '06-20-2026,BrokerageLink Test,111111111,REINVESTMENT as of 2026-06-19 FAKE CO (FAKE) (Cash),FAKE,FAKE CO,Cash,0,,USD,9.50,1.5,0,"","","",-14.25,""',
    '06-20-2026,BrokerageLink Test,111111111,DIVIDEND RECEIVED as of 2026-06-19 FAKE CO (FAKE) (Cash),FAKE,FAKE CO,Cash,0,,USD,"",0,0,"","","",14.25,""',
    '06-21-2026,Roth IRA Test,333333333,YOU SOLD OPENING TRANSACTION CALL (ZZZZ) ZZZZ JAN 15 27 $30 (100 SHS) (Cash), -ZZZZ270115C30,CALL (ZZZZ) ZZZZ JAN 15 27 $30,Cash,0,,USD,1.05,-2,0,1.3,0.03,"",208.67,06-23-2026',
    '06-22-2026,Roth IRA Test,333333333,YOU BOUGHT CLOSING TRANSACTION CALL (ZZZZ) ZZZZ JAN 15 27 $30 (100 SHS) (Cash), -ZZZZ270115C30,CALL (ZZZZ) ZZZZ JAN 15 27 $30,Cash,0,,USD,0.80,2,0,"",0.03,"",-160.03,06-24-2026',
    '',                       # blank line
    '"1038360.4.0"',         # footer id
    'Date downloaded 07/03/2026 09:54 am',   # footer disclaimer
]


class LoadHistoryTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(HEADER + "\n\n" + "\n".join(ROWS) + "\n")
        self.recs = history.load_history(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_footer_and_blank_skipped(self):
        # 6 real transactions; blank + 2 footer rows dropped.
        self.assertEqual(len(self.recs), 6)

    def test_dates_parsed(self):
        self.assertEqual(self.recs[0]["date"], dt.date(2026, 6, 10))

    def test_action_classification(self):
        kinds = [r["action_kind"] for r in self.recs]
        self.assertEqual(kinds, ["BUY", "SELL", "REINVEST", "DIVIDEND", "OPTION_OPEN", "OPTION_CLOSE"])

    def test_signed_vs_abs_qty(self):
        sell = self.recs[1]
        self.assertEqual(sell["signed_qty"], -40.0)
        self.assertEqual(sell["abs_qty"], 40.0)
        self.assertEqual(self.recs[0]["signed_qty"], 100.0)

    def test_reinvest_is_buy_dividend_is_not(self):
        self.assertIn(self.recs[2]["action_kind"], history.BUY_KINDS)      # REINVEST
        self.assertNotIn(self.recs[3]["action_kind"], history.BUY_KINDS)   # DIVIDEND

    def test_symbol_and_underlying(self):
        self.assertEqual(self.recs[0]["underlying"], "FAKE")
        self.assertEqual(self.recs[0]["kind"], "stock")
        opt = self.recs[4]
        self.assertEqual(opt["kind"], "option")
        self.assertEqual(opt["underlying"], "ZZZZ")

    def test_account_number_captured(self):
        self.assertEqual(self.recs[1]["account_number"], "222222222")
        self.assertEqual(self.recs[1]["account"], "Individual - TOD Test")

    def test_missing_header_raises(self):
        fd, bad = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("not a fidelity file\n1,2,3\n")
            with self.assertRaises(ValueError):
                history.load_history(bad)
        finally:
            os.unlink(bad)


if __name__ == "__main__":
    unittest.main()
