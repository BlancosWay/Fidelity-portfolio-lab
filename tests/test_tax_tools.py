"""Tests for scripts/analyze/tax_tools.py (stdlib unittest). Synthetic data only."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "analyze"))
import tax_tools as tt  # noqa: E402


def lot(account="Individual - TOD Test", symbol="AAPL", quantity=10.0, current_value=1000.0,
        gain_loss=-100.0, date_acquired="2026-01-15", term="Short-Term", cost_basis_total=1100.0,
        avg_cost_basis=110.0, gain_loss_pct=-9.0, description="", margin_cash="Margin"):
    return dict(account=account, symbol=symbol, quantity=quantity, current_value=current_value,
                gain_loss=gain_loss, date_acquired=date_acquired, term=term,
                cost_basis_total=cost_basis_total, avg_cost_basis=avg_cost_basis,
                gain_loss_pct=gain_loss_pct, description=description, margin_cash=margin_cash)


class IsTaxableTests(unittest.TestCase):
    def test_taxable(self):
        for a in ("Individual - TOD Test", "Individual - TOD 999", "Joint Brokerage Test", "Individual"):
            self.assertTrue(tt.is_taxable(a), a)

    def test_tax_advantaged(self):
        for a in ("Roth IRA Test", "Traditional IRA Test", "Health Savings Account Test",
                  "BrokerageLink Test", "Education 529 Test", "My 401k Test", "403(b) Test",
                  "HSA Test"):
            self.assertFalse(tt.is_taxable(a), a)

    def test_empty_defaults_taxable(self):
        self.assertTrue(tt.is_taxable(""))
        self.assertTrue(tt.is_taxable(None))


class IsCashTests(unittest.TestCase):
    def test_cash(self):
        self.assertTrue(tt.is_cash(lot(symbol="CASH")))
        self.assertTrue(tt.is_cash(lot(symbol=" cash ")))
        self.assertFalse(tt.is_cash(lot(symbol="AAPL")))


class SecurityKeyTests(unittest.TestCase):
    def test_stock(self):
        k = tt.security_key("AAPL")
        self.assertEqual((k["kind"], k["underlying"], k["key"]), ("stock", "AAPL", "AAPL"))
        self.assertEqual(tt.security_key("brk.b")["underlying"], "BRK.B")

    def test_positions_option(self):
        k = tt.security_key("AAL 20 Call")
        self.assertEqual((k["kind"], k["underlying"]), ("option", "AAL"))
        self.assertEqual(tt.security_key("GOOG 200 Put")["underlying"], "GOOG")

    def test_history_option(self):
        k = tt.security_key(" -SOFI270115C30")
        self.assertEqual((k["kind"], k["underlying"], k["key"]), ("option", "SOFI", "SOFI270115C30"))

    def test_underlying_links_stock_and_option(self):
        self.assertEqual(tt.security_key("AAL")["underlying"],
                         tt.security_key("AAL 20 Call")["underlying"])


class SafePerShareTests(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(tt.safe_per_share(lot(quantity=10, current_value=1000)), 100.0)

    def test_zero_or_missing(self):
        self.assertIsNone(tt.safe_per_share(lot(quantity=0, current_value=1000)))
        self.assertIsNone(tt.safe_per_share(lot(quantity=-5, current_value=1000)))
        self.assertIsNone(tt.safe_per_share(lot(quantity=None, current_value=1000)))
        self.assertIsNone(tt.safe_per_share(lot(quantity=10, current_value=None)))


class TaxableLossCandidateTests(unittest.TestCase):
    def test_filters(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="AAPL", gain_loss=-100.0),   # keep
            lot(account="Individual - TOD Test", symbol="MSFT", gain_loss=50.0),     # gain -> drop
            lot(account="Roth IRA Test", symbol="NVDA", gain_loss=-200.0),           # tax-adv -> drop
            lot(account="Individual - TOD Test", symbol="CASH", gain_loss=None),     # cash -> drop
            lot(account="Individual - TOD Test", symbol="TSLA", gain_loss=-1.0),     # keep
        ]
        got = {c["symbol"] for c in tt.taxable_loss_candidates(lots)}
        self.assertEqual(got, {"AAPL", "TSLA"})


class HarvestTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def test_ranking_and_summary(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="LTLOSS", gain_loss=-300.0,
                date_acquired="2024-01-05", term="Long-Term"),
            lot(account="Individual - TOD Test", symbol="STBIG", gain_loss=-500.0,
                date_acquired="2026-02-01", term="Short-Term"),
            lot(account="Individual - TOD Test", symbol="STSMALL", gain_loss=-50.0,
                date_acquired="2026-03-01", term="Short-Term"),
            lot(account="Roth IRA Test", symbol="IGNORED", gain_loss=-999.0,
                date_acquired="2026-01-01", term="Short-Term"),
            lot(account="Individual - TOD Test", symbol="GAIN", gain_loss=100.0,
                date_acquired="2026-01-01", term="Short-Term"),
        ]
        rows, s = tt.harvest(lots, self.AS_OF, st_rate=0.30, lt_rate=0.20)
        # ST first (biggest loss first), then LT; IRA + gain excluded.
        self.assertEqual([r["symbol"] for r in rows], ["STBIG", "STSMALL", "LTLOSS"])
        self.assertAlmostEqual(s["st_loss"], -550.0)
        self.assertAlmostEqual(s["lt_loss"], -300.0)
        self.assertEqual((s["st_lots"], s["lt_lots"]), (2, 1))
        self.assertAlmostEqual(s["est_benefit"], 550 * 0.30 + 300 * 0.20)  # positive avoided tax
        self.assertFalse(s["has_options"])

    def test_term_recomputed_from_date(self):
        # Stored term is a stale "Short-Term" but acquired > 1yr before as_of -> recomputed Long-Term.
        lots = [lot(account="Individual - TOD Test", symbol="STALE", gain_loss=-10.0,
                    date_acquired="2025-01-01", term="Short-Term")]
        rows, s = tt.harvest(lots, self.AS_OF)
        self.assertEqual(rows[0]["term"], "Long-Term")
        self.assertEqual(s["lt_lots"], 1)

    def test_option_flag(self):
        lots = [lot(account="Individual - TOD Test", symbol="AAL 20 Call", gain_loss=-5.0,
                    date_acquired="2026-06-01", term="Short-Term")]
        rows, s = tt.harvest(lots, self.AS_OF)
        self.assertTrue(rows[0]["is_option"])
        self.assertTrue(s["has_options"])


if __name__ == "__main__":
    unittest.main()
