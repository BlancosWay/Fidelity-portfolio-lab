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


def hrec(date, account, symbol, kind="BUY", qty=10.0, action=None):
    """Synthetic history record shaped like history.load_history output."""
    sk = tt.security_key(symbol)
    return dict(date=date, account=account, account_number="1", action=(action or kind), action_kind=kind,
                symbol=symbol, sec_key=sk["key"], kind=sk["kind"], underlying=sk["underlying"],
                signed_qty=qty, abs_qty=abs(qty), price=10.0, amount=-100.0)


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


class RipeningTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def test_winner_loser_and_filters(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="WIN", gain_loss=1000.0,
                date_acquired="2025-12-01", term="Short-Term"),
            lot(account="Individual - TOD Test", symbol="LOSE", gain_loss=-200.0,
                date_acquired="2026-01-15", term="Short-Term"),
            lot(account="Individual - TOD Test", symbol="LTOLD", gain_loss=500.0,
                date_acquired="2024-01-01", term="Long-Term"),        # long-term -> excluded
            lot(account="Roth IRA Test", symbol="IRA", gain_loss=10.0,
                date_acquired="2026-06-01", term="Short-Term"),       # tax-advantaged -> excluded
            lot(account="Individual - TOD Test", symbol="CASH", gain_loss=None,
                date_acquired="", term=""),                           # cash -> excluded
        ]
        rows, s = tt.ripening(lots, self.AS_OF, st_rate=0.30, lt_rate=0.20)
        self.assertEqual({r["symbol"] for r in rows}, {"WIN", "LOSE"})
        win = next(r for r in rows if r["symbol"] == "WIN")
        self.assertEqual(win["ripens_on"], "2026-12-02")
        self.assertEqual(win["hint"], "wait for LT")
        self.assertAlmostEqual(win["tax_saved_by_waiting"], 1000 * (0.30 - 0.20))
        lose = next(r for r in rows if r["symbol"] == "LOSE")
        self.assertEqual(lose["hint"], "HARVEST BEFORE RIPENING")
        self.assertEqual((s["winners"], s["losers"]), (1, 1))

    def test_leap_boundary(self):
        lots = [lot(account="Individual - TOD Test", symbol="LEAP", gain_loss=5.0,
                    date_acquired="2024-02-29", term="Short-Term")]
        rows, _ = tt.ripening(lots, dt.date(2025, 2, 28))
        self.assertEqual(rows[0]["ripens_on"], "2025-03-01")  # first long-term day
        self.assertEqual(rows[0]["days_until"], 1)

    def test_within_filter_and_order(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="SOON", gain_loss=1.0,
                date_acquired="2025-07-10", term="Short-Term"),
            lot(account="Individual - TOD Test", symbol="LATER", gain_loss=1.0,
                date_acquired="2026-01-10", term="Short-Term"),
        ]
        rows, _ = tt.ripening(lots, self.AS_OF, within=30)
        self.assertEqual([r["symbol"] for r in rows], ["SOON"])

    def test_ignores_stale_stored_term(self):
        # Term is recomputed from the date, not trusted from the stored column.
        lots = [
            lot(account="Individual - TOD Test", symbol="ACTUALLY_LT", gain_loss=5.0,
                date_acquired="2025-01-01", term="Short-Term"),   # really long-term now -> excluded
            lot(account="Individual - TOD Test", symbol="ACTUALLY_ST", gain_loss=5.0,
                date_acquired="2026-06-01", term="Long-Term"),    # really short-term -> included
        ]
        rows, _ = tt.ripening(lots, self.AS_OF)
        self.assertEqual([r["symbol"] for r in rows], ["ACTUALLY_ST"])


class ConcentrationTests(unittest.TestCase):
    def test_aggregate_and_metrics(self):
        lots = [
            lot(account="A", symbol="AAA", current_value=6000.0),
            lot(account="B", symbol="AAA", current_value=2000.0),   # AAA = 8000 across 2 accounts
            lot(account="A", symbol="BBB", current_value=2000.0),
            lot(account="A", symbol="CASH", current_value=1000.0, gain_loss=None),  # cash excluded
        ]
        rows, s = tt.concentration(lots, top=10, threshold=0.5)
        self.assertAlmostEqual(s["invested_total"], 10000.0)
        self.assertAlmostEqual(s["cash_total"], 1000.0)
        self.assertEqual([r["symbol"] for r in rows], ["AAA", "BBB"])   # ranked by value
        aaa = rows[0]
        self.assertAlmostEqual(aaa["value"], 8000.0)
        self.assertEqual(aaa["accounts"], 2)
        self.assertAlmostEqual(aaa["weight"], 0.8)
        self.assertAlmostEqual(s["hhi"], 0.8 ** 2 + 0.2 ** 2)          # 0.68
        self.assertAlmostEqual(s["effective_positions"], 1 / 0.68)
        self.assertEqual(s["over_threshold"], ["AAA"])                  # 0.8 > 0.5

    def test_all_cash_guard(self):
        rows, s = tt.concentration([lot(account="A", symbol="CASH", current_value=500.0, gain_loss=None)])
        self.assertEqual(rows, [])
        self.assertEqual(s["num_positions"], 0)
        self.assertEqual(s["hhi"], 0.0)
        self.assertIsNone(s["effective_positions"])
        self.assertAlmostEqual(s["cash_pct"], 1.0)

    def test_zero_invested_nonzero_symbols_guard(self):
        # Non-cash symbols exist but sum to zero invested -> still the empty-rankings guard.
        lots = [
            lot(account="A", symbol="ZERO1", current_value=0.0),
            lot(account="A", symbol="ZERO2", current_value=0.0),
            lot(account="A", symbol="CASH", current_value=300.0, gain_loss=None),
        ]
        rows, s = tt.concentration(lots)
        self.assertEqual(rows, [])
        self.assertEqual(s["num_positions"], 0)
        self.assertIsNone(s["effective_positions"])
        self.assertAlmostEqual(s["cash_pct"], 1.0)


class SelectLotsTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def _lots(self):
        # symbol XYZ, price $10/sh (current_value 100 / qty 10); varying cost & term.
        return [
            lot(account="Individual - TOD Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=12.0, cost_basis_total=120.0, gain_loss=-20.0,
                date_acquired="2026-06-01", term="Short-Term"),   # ST loss, gain/sh -2
            lot(account="Individual - TOD Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=9.0, cost_basis_total=90.0, gain_loss=10.0,
                date_acquired="2026-06-15", term="Short-Term"),   # ST gain, gain/sh +1
            lot(account="Individual - TOD Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                date_acquired="2024-01-01", term="Long-Term"),    # LT gain, gain/sh +2
        ]

    def test_hifo(self):
        picks, s = tt.select_lots(self._lots(), "XYZ", 15, "hifo", as_of=self.AS_OF)
        self.assertAlmostEqual(picks[0]["cost"], 12.0)   # highest cost first
        self.assertAlmostEqual(picks[1]["cost"], 9.0)
        self.assertAlmostEqual(s["realized_gain"], -15.0)

    def test_fifo(self):
        picks, s = tt.select_lots(self._lots(), "XYZ", 15, "fifo", as_of=self.AS_OF)
        self.assertEqual(picks[0]["acquired"], "2024-01-01")  # oldest first
        self.assertAlmostEqual(s["realized_gain"], 10.0)

    def test_loss_first(self):
        picks, _ = tt.select_lots(self._lots(), "XYZ", 5, "loss-first", as_of=self.AS_OF)
        self.assertAlmostEqual(picks[0]["per_share_gain"], -2.0)

    def test_min_tax_orders_by_impact(self):
        # min-tax: A (ST loss) first, then C (LT gain, impact 2*0.15=0.30) before B (ST gain, 1*0.32=0.32).
        picks, s = tt.select_lots(self._lots(), "XYZ", 15, "min-tax", st_rate=0.32, lt_rate=0.15, as_of=self.AS_OF)
        self.assertAlmostEqual(picks[0]["cost"], 12.0)      # A (loss) first
        self.assertEqual(picks[1]["term"], "Long-Term")     # small-impact LT gain before ST gain
        self.assertAlmostEqual(s["realized_gain"], -10.0)
        self.assertAlmostEqual(s["delta_vs_fifo"], -20.0)   # -10 vs FIFO +10

    def test_fractional_and_insufficient(self):
        _, s = tt.select_lots(self._lots(), "XYZ", 100, "fifo", as_of=self.AS_OF)
        self.assertTrue(s["insufficient"])
        self.assertAlmostEqual(s["available_shares"], 30.0)
        self.assertAlmostEqual(s["filled_shares"], 30.0)
        picks2, _ = tt.select_lots(self._lots(), "XYZ", 12.5, "hifo", as_of=self.AS_OF)
        self.assertAlmostEqual(sum(p["qty_used"] for p in picks2), 12.5)

    def test_skips_zero_qty_lot(self):
        lots = self._lots() + [lot(account="Individual - TOD Test", symbol="XYZ", quantity=0,
                                    current_value=0.0, avg_cost_basis=5.0, gain_loss=0.0,
                                    date_acquired="2026-01-01", term="Short-Term")]
        picks, _ = tt.select_lots(lots, "XYZ", 30, "fifo", as_of=self.AS_OF)
        self.assertEqual(len(picks), 3)  # zero-qty lot skipped (safe_per_share -> None)


class WashSaleTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def cand(self, symbol="AAA", account="Individual - TOD Test"):
        return lot(account=account, symbol=symbol, gain_loss=-100.0,
                   date_acquired="2026-06-15", term="Short-Term")

    def test_caution_taxable_buy_in_window(self):
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        self.assertEqual(res["candidates"][0]["status"], "CAUTION")
        self.assertEqual(len(res["candidates"][0]["triggers"]), 1)

    def test_blocked_ira_buy(self):
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "Roth IRA Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        self.assertEqual(res["candidates"][0]["status"], "BLOCKED")   # permanent disallowance

    def test_window_boundary(self):
        c = [self.cand("AAA")]
        in30 = [hrec(self.AS_OF - dt.timedelta(days=30), "Individual - TOD Test", "AAA")]
        out31 = [hrec(self.AS_OF - dt.timedelta(days=31), "Individual - TOD Test", "AAA")]
        self.assertEqual(tt.washsale(c, in30, self.AS_OF, 30)["candidates"][0]["status"], "CAUTION")
        self.assertEqual(tt.washsale(c, out31, self.AS_OF, 30)["candidates"][0]["status"], "CLEAN")

    def test_reinvest_triggers_dividend_does_not(self):
        c = [self.cand("AAA")]
        rei = [hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "AAA", "REINVEST")]
        div = [hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "AAA", "DIVIDEND")]
        self.assertEqual(tt.washsale(c, rei, self.AS_OF, 30)["candidates"][0]["status"], "CAUTION")
        self.assertEqual(tt.washsale(c, div, self.AS_OF, 30)["candidates"][0]["status"], "CLEAN")

    def test_same_underlying_option(self):
        c = [self.cand("AAA")]
        hist = [hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "AAA 30 Call", "BUY")]
        self.assertEqual(tt.washsale(c, hist, self.AS_OF, 30, same_underlying=False)["candidates"][0]["status"], "CLEAN")
        self.assertEqual(tt.washsale(c, hist, self.AS_OF, 30, same_underlying=True)["candidates"][0]["status"], "CAUTION")

    def test_realized_review_loss_unknown(self):
        hist = [
            hrec(dt.date(2026, 6, 10), "Individual - TOD Test", "BBB", "SELL", qty=-5),
            hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "BBB", "BUY"),
        ]
        res = tt.washsale([], hist, self.AS_OF, 30)
        self.assertEqual(len(res["realized"]), 1)
        self.assertIn("loss unknown", res["realized"][0]["status"])

    def test_option_buy_to_open_same_underlying(self):
        c = [self.cand("AAA")]  # stock loss on AAA
        bto = [hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "AAA 30 Call",
                    kind="OPTION_OPEN", action="YOU BOUGHT OPENING TRANSACTION CALL (AAA)")]
        sto = [hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "AAA 30 Call",
                    kind="OPTION_OPEN", action="YOU SOLD OPENING TRANSACTION CALL (AAA)")]
        # buy-to-open a same-underlying option -> CAUTION under --same-underlying
        self.assertEqual(tt.washsale(c, bto, self.AS_OF, 30, same_underlying=True)["candidates"][0]["status"], "CAUTION")
        # without the flag, an option is not the same security -> CLEAN
        self.assertEqual(tt.washsale(c, bto, self.AS_OF, 30, same_underlying=False)["candidates"][0]["status"], "CLEAN")
        # writing the option (sell-to-open) is not an acquisition -> CLEAN even with the flag
        self.assertEqual(tt.washsale(c, sto, self.AS_OF, 30, same_underlying=True)["candidates"][0]["status"], "CLEAN")


if __name__ == "__main__":
    unittest.main()
