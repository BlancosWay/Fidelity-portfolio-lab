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
        # No offsetting gains -> the whole -850 net loss deducts against ordinary income (under the
        # $3k cap) at the ordinary/ST rate: 850 * 0.30 = 255 (the LT portion is NOT valued at lt_rate
        # when it merely offsets ordinary income).
        self.assertAlmostEqual(s["est_benefit"], 850 * 0.30)
        self.assertAlmostEqual(s["carryforward_loss"], 0.0)
        self.assertFalse(s["has_options"])

    def test_benefit_capped_at_3k_without_gains(self):
        lots = [lot(account="Individual - TOD Test", symbol=f"L{i}", gain_loss=-50000.0,
                    date_acquired="2024-01-01", term="Long-Term") for i in range(3)]
        _, s = tt.harvest(lots, self.AS_OF, st_rate=0.32, lt_rate=0.15)
        self.assertAlmostEqual(s["est_benefit"], 3000 * 0.32)          # capped ordinary offset
        self.assertAlmostEqual(s["carryforward_loss"], 150000 - 3000)  # rest carries forward

    def test_benefit_uses_lt_rate_against_lt_gains(self):
        # A harvested LT loss that offsets a known LT gain saves only lt_rate, not st_rate.
        lots = [lot(account="Individual - TOD Test", symbol="LTL", gain_loss=-4000.0,
                    date_acquired="2024-01-01", term="Long-Term")]
        _, s = tt.harvest(lots, self.AS_OF, st_rate=0.32, lt_rate=0.15, offsetting_lt_gains=10000.0)
        self.assertAlmostEqual(s["est_benefit"], 4000 * 0.15)

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

    def test_option_uses_total_basis_not_per_share_avg(self):
        # Option lot: qty 2 contracts, current_value 1600, cost_basis_total 2500, avg_cost_basis 12.50
        # (premium per underlying share). Selling 1 contract realizes (1600-2500)/2 = -450, NOT the
        # +787.50 you'd get by mixing per-contract proceeds with per-share cost.
        lots = [lot(account="Individual - TOD Test", symbol="AAA 20 Call", quantity=2,
                    current_value=1600.0, cost_basis_total=2500.0, avg_cost_basis=12.50,
                    gain_loss=-900.0, date_acquired="2026-06-01", term="Short-Term")]
        picks, s = tt.select_lots(lots, "AAA 20 Call", 1, "fifo", as_of=self.AS_OF)
        self.assertAlmostEqual(picks[0]["realized_gain"], -450.0)
        self.assertAlmostEqual(s["realized_gain"], -450.0)

    def test_excludes_tax_advantaged_accounts(self):
        # A Roth lot of the same symbol must never be a sale candidate (its gains are tax-free and a
        # specific-ID sale there isn't a tax-optimized taxable sale).
        lots = [
            lot(account="Individual - TOD Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                date_acquired="2024-01-01", term="Long-Term"),
            lot(account="Roth IRA Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                date_acquired="2024-01-01", term="Long-Term"),
        ]
        picks, s = tt.select_lots(lots, "XYZ", 20, "min-tax", as_of=self.AS_OF)
        self.assertTrue(all(tt.is_taxable(p["account"]) for p in picks))
        self.assertAlmostEqual(s["available_shares"], 10.0)   # only the taxable lot
        self.assertTrue(s["insufficient"])                    # can't fill 20 from 10 taxable shares
        self.assertFalse(s["multi_account"])

    def test_pure_tax_advantaged_symbol_yields_no_picks(self):
        lots = [lot(account="Roth IRA Test", symbol="XYZ", quantity=10, current_value=100.0,
                    avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                    date_acquired="2024-01-01", term="Long-Term")]
        picks, s = tt.select_lots(lots, "XYZ", 5, "min-tax", as_of=self.AS_OF)
        self.assertEqual(picks, [])
        self.assertAlmostEqual(s["available_shares"], 0.0)

    def test_multi_account_flag(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                date_acquired="2024-01-01", term="Long-Term"),
            lot(account="Joint Brokerage Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                date_acquired="2024-01-01", term="Long-Term"),
        ]
        picks, s = tt.select_lots(lots, "XYZ", 20, "fifo", as_of=self.AS_OF)
        self.assertTrue(s["multi_account"])
        self.assertEqual(sorted(s["accounts"]), ["Individual - TOD Test", "Joint Brokerage Test"])

    def test_mixed_none_and_named_account_no_crash(self):
        # is_taxable(None) is True, so a None-account lot is sellable; sorting accounts must not crash.
        lots = [
            lot(account=None, symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                date_acquired="2024-01-01", term="Long-Term"),
            lot(account="Individual - TOD Test", symbol="XYZ", quantity=10, current_value=100.0,
                avg_cost_basis=8.0, cost_basis_total=80.0, gain_loss=20.0,
                date_acquired="2024-01-01", term="Long-Term"),
        ]
        picks, s = tt.select_lots(lots, "XYZ", 20, "fifo", as_of=self.AS_OF)
        self.assertTrue(s["multi_account"])
        self.assertEqual(len(picks), 2)


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

    def test_brokeragelink_401k_buy_is_review_not_blocked(self):
        # BUG REPRO: a replacement buy in a 401(k)/BrokerageLink has NO IRS wash-sale guidance and the
        # prevailing view is the rule does NOT apply -> status should be REVIEW, not BLOCKED. The current
        # code keys off is_taxable (any tax-advantaged account) and returns BLOCKED, which over-flags it.
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "BrokerageLink Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        self.assertEqual(res["candidates"][0]["status"], "REVIEW")

    def test_hsa_buy_blocked(self):
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "Health Savings Account Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        self.assertEqual(res["candidates"][0]["status"], "BLOCKED")

    def test_529_buy_review(self):
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "Education 529 Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        self.assertEqual(res["candidates"][0]["status"], "REVIEW")

    def test_trigger_records_category_and_severity(self):
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "BrokerageLink Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        trig = res["candidates"][0]["triggers"][0]
        self.assertEqual(trig["category"], "employer")
        self.assertEqual(trig["severity"], "REVIEW")

    def test_precedence_ira_plus_employer_is_blocked(self):
        # worst severity among triggers wins: BLOCKED (ira) > REVIEW (employer)
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "Roth IRA Test", "AAA", "BUY"),
                           hrec(dt.date(2026, 6, 21), "BrokerageLink Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        self.assertEqual(res["candidates"][0]["status"], "BLOCKED")

    def test_precedence_taxable_plus_employer_is_caution(self):
        # CAUTION (taxable) > REVIEW (employer)
        res = tt.washsale([self.cand("AAA")],
                          [hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "AAA", "BUY"),
                           hrec(dt.date(2026, 6, 21), "BrokerageLink Test", "AAA", "BUY")],
                          self.AS_OF, window=30)
        self.assertEqual(res["candidates"][0]["status"], "CAUTION")

    def test_summary_counts_review(self):
        res = tt.washsale(
            [self.cand("AAA"), self.cand("BBB"), self.cand("CCC"), self.cand("DDD")],
            [hrec(dt.date(2026, 6, 20), "Roth IRA Test", "AAA", "BUY"),          # BLOCKED
             hrec(dt.date(2026, 6, 20), "Individual - TOD Test", "BBB", "BUY"),  # CAUTION
             hrec(dt.date(2026, 6, 20), "BrokerageLink Test", "CCC", "BUY")],    # REVIEW; DDD -> CLEAN
            self.AS_OF, window=30)
        s = res["summary"]
        self.assertEqual((s["blocked"], s["caution"], s["review"], s["clean"]), (1, 1, 1, 1))


class WashCategoryTests(unittest.TestCase):
    def test_each_category(self):
        cases = {
            "Roth IRA Test": "ira",
            "Traditional IRA Test": "ira",
            "Rollover IRA 123": "ira",
            "Health Savings Account Test": "hsa",
            "My HSA Test": "hsa",
            "BrokerageLink Test": "employer",
            "My 401k Test": "employer",
            "401(k) Plan": "employer",
            "403(b) Test": "employer",
            "Education 529 Test": "529",
            "Individual - TOD Test": "taxable",
            "Joint Brokerage Test": "taxable",
            "": "taxable",
        }
        for account, expected in cases.items():
            self.assertEqual(tt.wash_category(account), expected, account)

    def test_employer_matched_before_ira(self):
        # A Roth/Traditional prefix on an employer plan must NOT fall through to the IRA bucket
        # (that would re-block the reported bug's common variant).
        self.assertEqual(tt.wash_category("Roth IRA Test"), "ira")
        self.assertEqual(tt.wash_category("Roth 401(k) Test"), "employer")
        self.assertEqual(tt.wash_category("BrokerageLink Roth 401(k) Test"), "employer")

    def test_severity_map(self):
        self.assertEqual(tt.WASH_SEVERITY["ira"], "BLOCKED")
        self.assertEqual(tt.WASH_SEVERITY["hsa"], "BLOCKED")
        self.assertEqual(tt.WASH_SEVERITY["employer"], "REVIEW")
        self.assertEqual(tt.WASH_SEVERITY["529"], "REVIEW")
        self.assertEqual(tt.WASH_SEVERITY["taxable"], "CAUTION")


class CapacityTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def lt_gain(self, symbol, gain, pct=100.0, account="Individual - TOD Test", qty=100.0):
        """A synthetic taxable LONG-TERM gain lot (acquired > 1yr before AS_OF)."""
        value = 2000.0 + gain
        return lot(account=account, symbol=symbol, quantity=qty, current_value=value,
                   gain_loss=float(gain), date_acquired="2024-01-15", term="Long-Term",
                   cost_basis_total=value - gain, avg_cost_basis=(value - gain) / qty, gain_loss_pct=pct)

    def test_headroom_budget_partial_fill(self):
        lots = [self.lt_gain("GAINA", 8000, pct=400.0), self.lt_gain("GAINB", 5000, pct=100.0)]
        picks, s = tt.gain_capacity(lots, self.AS_OF, income=40000, ceiling=50000)
        self.assertEqual(s["source"], "headroom")
        self.assertEqual(s["budget"], 10000)
        self.assertAlmostEqual(s["realized"], 10000)
        self.assertEqual(s["constrained_by"], "budget")
        self.assertAlmostEqual(s["est_tax"], 0.0)              # default within_rate 0.0 (0% LTCG)
        self.assertAlmostEqual(s["remaining_budget"], 0.0)
        self.assertEqual(len(picks), 2)
        self.assertFalse(picks[0]["partial"])                  # GAINA (biggest gain) whole
        self.assertTrue(picks[1]["partial"])                   # GAINB partial
        self.assertAlmostEqual(picks[1]["gain_used"], 2000.0)
        self.assertAlmostEqual(picks[1]["qty_used"], 40.0)     # 100 * (2000/5000)

    def test_within_rate_taxes_the_gain(self):
        lots = [self.lt_gain("GAINA", 8000), self.lt_gain("GAINB", 5000)]
        _, s = tt.gain_capacity(lots, self.AS_OF, income=40000, ceiling=50000, within_rate=0.15)
        self.assertAlmostEqual(s["est_tax"], 10000 * 0.15)     # NIIT/IRMAA ceiling: gain still taxed

    def test_inventory_constrained(self):
        picks, s = tt.gain_capacity([self.lt_gain("GAINA", 8000)], self.AS_OF, income=30000, ceiling=50000)
        self.assertAlmostEqual(s["realized"], 8000)
        self.assertEqual(s["constrained_by"], "inventory")
        self.assertAlmostEqual(s["leftover_gain"], 0.0)
        self.assertAlmostEqual(s["remaining_budget"], 12000)

    def test_target_gain_mode(self):
        picks, s = tt.gain_capacity([self.lt_gain("GAINA", 8000)], self.AS_OF, target_gain=5000)
        self.assertEqual(s["source"], "target-gain")
        self.assertAlmostEqual(s["realized"], 5000)
        self.assertTrue(picks[0]["partial"])
        self.assertAlmostEqual(s["est_tax"], 5000 * 0.15)

    def test_excludes_non_candidates(self):
        lots = [
            self.lt_gain("GAINA", 8000),                                                  # candidate
            lot(symbol="STGAIN", date_acquired="2026-06-01", term="Short-Term",
                cost_basis_total=1000.0, current_value=1500.0, gain_loss=500.0, gain_loss_pct=50.0),
            lot(symbol="LTLOSS", date_acquired="2024-01-01", term="Long-Term",
                cost_basis_total=2000.0, current_value=1000.0, gain_loss=-1000.0, gain_loss_pct=-50.0),
            self.lt_gain("OPTX 30 Call", 3000),                                           # option
            self.lt_gain("IRAG", 4000, account="Roth IRA Test"),                          # tax-advantaged
        ]
        _, s = tt.gain_capacity(lots, self.AS_OF, income=0, ceiling=100000)
        self.assertEqual(s["n_candidates"], 1)
        self.assertAlmostEqual(s["available_gain"], 8000)

    def test_inventory_only(self):
        picks, s = tt.gain_capacity([self.lt_gain("GAINA", 8000), self.lt_gain("GAINB", 5000)], self.AS_OF)
        self.assertEqual(picks, [])
        self.assertEqual(s["source"], "inventory-only")
        self.assertAlmostEqual(s["available_gain"], 13000)
        self.assertIsNone(s["est_tax"])

    def test_above_ceiling(self):
        picks, s = tt.gain_capacity([self.lt_gain("GAINA", 8000)], self.AS_OF, income=60000, ceiling=50000)
        self.assertEqual(picks, [])
        self.assertTrue(s["above_ceiling"])
        self.assertAlmostEqual(s["realized"], 0.0)
        self.assertEqual(s["budget"], 0.0)


class GiftTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def lt(self, symbol, gain, pct, account="Individual - TOD Test", acquired="2024-01-15"):
        value = 1000.0 + gain
        return lot(account=account, symbol=symbol, quantity=10.0, current_value=value,
                   gain_loss=float(gain), date_acquired=acquired, term="Long-Term",
                   cost_basis_total=value - gain, avg_cost_basis=(value - gain) / 10.0, gain_loss_pct=pct)

    def test_lt_gain_is_candidate(self):
        rows, s = tt.gift_candidates([self.lt("DON", 300, 30.0)], self.AS_OF)
        self.assertEqual(s["n_candidates"], 1)
        self.assertAlmostEqual(rows[0]["tax_avoided"], 300 * 0.15)
        self.assertAlmostEqual(s["total_gain"], 300)
        self.assertAlmostEqual(s["total_fmv"], 1300)

    def test_min_gain_pct_filters(self):
        _, s = tt.gift_candidates([self.lt("DON", 300, 30.0)], self.AS_OF, min_gain_pct=40)
        self.assertEqual(s["n_candidates"], 0)

    def test_short_term_and_loss_excluded_and_counted(self):
        lots = [
            self.lt("DON", 300, 30.0),
            lot(symbol="STG", date_acquired="2026-06-01", term="Short-Term",
                cost_basis_total=1000.0, current_value=1300.0, gain_loss=300.0, gain_loss_pct=30.0),
            lot(symbol="LOSS", date_acquired="2024-01-01", term="Long-Term",
                cost_basis_total=2000.0, current_value=1500.0, gain_loss=-500.0, gain_loss_pct=-25.0),
        ]
        _, s = tt.gift_candidates(lots, self.AS_OF)
        self.assertEqual(s["n_candidates"], 1)
        self.assertEqual(s["n_short_term_gain"], 1)
        self.assertEqual(s["n_loss"], 1)

    def test_option_and_ira_excluded(self):
        lots = [self.lt("OPT 30 Call", 300, 30.0), self.lt("IRAD", 300, 30.0, account="Roth IRA Test")]
        _, s = tt.gift_candidates(lots, self.AS_OF)
        self.assertEqual(s["n_candidates"], 0)

    def test_sort_highest_gain_pct_first(self):
        rows, _ = tt.gift_candidates([self.lt("LOWP", 900, 20.0), self.lt("HIGHP", 300, 300.0)], self.AS_OF)
        self.assertEqual([r["symbol"] for r in rows], ["HIGHP", "LOWP"])

    def test_stale_stored_term_recomputed(self):
        stale = lot(symbol="STALE", date_acquired="2026-06-01", term="Long-Term",
                    cost_basis_total=1000.0, current_value=1300.0, gain_loss=300.0, gain_loss_pct=30.0)
        _, s = tt.gift_candidates([stale], self.AS_OF)
        self.assertEqual(s["n_candidates"], 0)          # recompute_term => Short-Term, not a candidate
        self.assertEqual(s["n_short_term_gain"], 1)

    def test_uncomputable_pct(self):
        nopct = lot(symbol="NOPCT", date_acquired="2024-01-01", term="Long-Term",
                    cost_basis_total=None, current_value=1000.0, gain_loss=1000.0, gain_loss_pct=None)
        rows, s = tt.gift_candidates([nopct], self.AS_OF)                 # default threshold -> included
        self.assertEqual(s["n_candidates"], 1)
        self.assertIsNone(rows[0]["gain_pct"])
        _, s2 = tt.gift_candidates([nopct], self.AS_OF, min_gain_pct=10)  # positive threshold -> excluded
        self.assertEqual(s2["n_candidates"], 0)


class DashboardTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def test_unrealized_by_account(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="A", date_acquired="2024-01-01", term="Long-Term",
                cost_basis_total=1000, current_value=1500, gain_loss=500, gain_loss_pct=50),
            lot(account="Individual - TOD Test", symbol="B", date_acquired="2026-06-01", term="Short-Term",
                cost_basis_total=1000, current_value=800, gain_loss=-200, gain_loss_pct=-20),
            lot(account="Roth IRA Test", symbol="C", date_acquired="2024-01-01", term="Long-Term",
                cost_basis_total=500, current_value=900, gain_loss=400, gain_loss_pct=80),
            lot(account="Individual - TOD Test", symbol="CASH", quantity="", date_acquired="", term="",
                cost_basis_total=None, current_value=500, gain_loss=None, gain_loss_pct=None,
                description="Cash HELD IN MONEY MARKET", margin_cash=""),
        ]
        rows, s = tt.unrealized_by_account(lots, self.AS_OF)
        tod = next(r for r in rows if r["account"] == "Individual - TOD Test")
        self.assertAlmostEqual(tod["lt_gl"], 500)
        self.assertAlmostEqual(tod["st_gl"], -200)
        self.assertAlmostEqual(s["taxable_lt"], 500)
        self.assertAlmostEqual(s["taxable_st"], -200)
        self.assertAlmostEqual(s["adv_lt"], 400)          # Roth IRA
        self.assertEqual(len(rows), 2)                    # cash excluded from G/L accounts

    def test_unrealized_stale_stored_term(self):
        stale = lot(account="Individual - TOD Test", symbol="S", date_acquired="2026-06-01",
                    term="Long-Term", cost_basis_total=1000, current_value=1200, gain_loss=200, gain_loss_pct=20)
        _, s = tt.unrealized_by_account([stale], self.AS_OF)
        self.assertAlmostEqual(s["taxable_st"], 200)      # recompute_term => Short-Term
        self.assertAlmostEqual(s["taxable_lt"], 0.0)

    def test_liquidation_estimate(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="A", date_acquired="2024-01-01", term="Long-Term",
                cost_basis_total=1000, current_value=3000, gain_loss=2000, gain_loss_pct=200),
            lot(account="Individual - TOD Test", symbol="B", date_acquired="2026-06-01", term="Short-Term",
                cost_basis_total=1000, current_value=1500, gain_loss=500, gain_loss_pct=50),
            lot(account="Roth IRA Test", symbol="C", date_acquired="2024-01-01", term="Long-Term",
                cost_basis_total=500, current_value=900, gain_loss=400, gain_loss_pct=80),
        ]
        le = tt.liquidation_estimate(lots, self.AS_OF, st_rate=0.32, lt_rate=0.15)
        self.assertAlmostEqual(le["st_gain"], 500)
        self.assertAlmostEqual(le["lt_gain"], 2000)       # IRA excluded
        self.assertAlmostEqual(le["est_tax"], 500 * 0.32 + 2000 * 0.15)
        self.assertEqual(le["n_lots"], 2)

    def test_liquidation_nets_st_loss_against_lt_gain(self):
        lots = [
            lot(account="Individual - TOD Test", symbol="STL", date_acquired="2026-06-01",
                term="Short-Term", gain_loss=-10000, current_value=0, cost_basis_total=10000),
            lot(account="Individual - TOD Test", symbol="LTG", date_acquired="2024-01-01",
                term="Long-Term", gain_loss=10000, current_value=20000, cost_basis_total=10000),
        ]
        le = tt.liquidation_estimate(lots, self.AS_OF, st_rate=0.32, lt_rate=0.15)
        self.assertAlmostEqual(le["est_tax"], 0.0)        # ST loss fully offsets LT gain -> ~0, never negative
        self.assertGreaterEqual(le["est_tax"], 0.0)

    def test_liquidation_net_loss_capped_with_carryforward(self):
        lots = [lot(account="Individual - TOD Test", symbol="BIGL", date_acquired="2024-01-01",
                    term="Long-Term", gain_loss=-50000, current_value=0, cost_basis_total=50000)]
        le = tt.liquidation_estimate(lots, self.AS_OF, st_rate=0.32, lt_rate=0.15)
        self.assertAlmostEqual(le["est_tax"], -(3000 * 0.32))   # benefit capped at $3k ordinary offset
        self.assertAlmostEqual(le["deductible_loss"], 3000)
        self.assertAlmostEqual(le["carryforward"], 47000)


class NetCapitalTaxTests(unittest.TestCase):
    def test_both_gains(self):
        r = tt._net_capital_tax(5000, 10000, 0.32, 0.15)
        self.assertAlmostEqual(r["est_tax"], 5000 * 0.32 + 10000 * 0.15)

    def test_st_gain_lt_loss_nets_to_winner_rate(self):
        r = tt._net_capital_tax(5000, -2000, 0.32, 0.15)   # net +3000, ST wins
        self.assertAlmostEqual(r["est_tax"], 3000 * 0.32)
        self.assertAlmostEqual(r["net_gain"], 3000)

    def test_lt_gain_st_loss_nets_to_lt_rate(self):
        r = tt._net_capital_tax(-4000, 10000, 0.32, 0.15)  # net +6000, LT wins
        self.assertAlmostEqual(r["est_tax"], 6000 * 0.15)

    def test_exact_cancel_is_zero(self):
        r = tt._net_capital_tax(-10000, 10000, 0.32, 0.15)
        self.assertAlmostEqual(r["est_tax"], 0.0)

    def test_net_loss_capped(self):
        r = tt._net_capital_tax(-1000, -5000, 0.32, 0.15)  # net -6000
        self.assertAlmostEqual(r["est_tax"], -(3000 * 0.32))
        self.assertAlmostEqual(r["deductible_loss"], 3000)
        self.assertAlmostEqual(r["carryforward"], 3000)

    def test_small_net_loss_under_cap(self):
        r = tt._net_capital_tax(0, -850, 0.30, 0.20)
        self.assertAlmostEqual(r["est_tax"], -(850 * 0.30))
        self.assertAlmostEqual(r["carryforward"], 0.0)

    def test_all_zero(self):
        r = tt._net_capital_tax(0, 0, 0.32, 0.15)
        self.assertAlmostEqual(r["est_tax"], 0.0)


def opt_lot(symbol, expiry_desc, contracts, current_value=1000.0, cost=800.0,
            account="Individual - TOD Test"):
    """Synthetic OPTION lot: symbol='AAL 17 Call', description=expiry, quantity=contracts (signed)."""
    q = float(contracts)
    return lot(account=account, symbol=symbol, quantity=q, current_value=current_value,
               gain_loss=(current_value - cost) if current_value is not None else 0.0,
               date_acquired="2026-03-01", term="Short-Term", cost_basis_total=cost,
               avg_cost_basis=(cost / abs(q)) if q else 0.0, gain_loss_pct=0.0,
               description=expiry_desc, margin_cash="Margin")


def stock_lot(symbol, qty, value, account="Individual - TOD Test"):
    """Synthetic STOCK lot used as a spot source (current price = value/qty)."""
    return lot(account=account, symbol=symbol, quantity=float(qty), current_value=float(value),
               gain_loss=0.0, date_acquired="2024-01-01", term="Long-Term",
               cost_basis_total=float(value), avg_cost_basis=value / qty, gain_loss_pct=0.0)


class OptionsTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def test_parse_option_positions_style(self):
        po = tt.parse_option(opt_lot("AAL 17 Call", "Jul-17-2026", 5))
        self.assertEqual(po["underlying"], "AAL")
        self.assertEqual(po["strike"], 17.0)
        self.assertEqual(po["type"], "call")
        self.assertEqual(po["expiry"], dt.date(2026, 7, 17))
        self.assertEqual(po["contracts"], 5.0)
        self.assertTrue(po["long"])
        self.assertEqual(po["multiplier"], 100)

    def test_parse_option_put_single_letter_short(self):
        self.assertEqual(tt.parse_option(opt_lot("AMZN 175 Put", "Aug-21-2026", 10))["type"], "put")
        self.assertEqual(tt.parse_option(opt_lot("S 20 Call", "Jul-17-2026", 1))["underlying"], "S")
        self.assertFalse(tt.parse_option(opt_lot("AAL 17 Call", "Jul-17-2026", -2))["long"])

    def test_parse_option_non_option_returns_none(self):
        self.assertIsNone(tt.parse_option(stock_lot("AAPL", 10, 1000)))

    def test_parse_option_full_contract_name_description(self):
        # Fidelity also exports the full contract name as the description; extract the embedded date.
        po = tt.parse_option(opt_lot("AAPL 250 Call", "AAPL JAN 16 2026 $250 CALL", 2))
        self.assertEqual(po["underlying"], "AAPL")
        self.assertEqual(po["strike"], 250.0)
        self.assertEqual(po["expiry"], dt.date(2026, 1, 16))

    def test_parse_option_occ_and_parity(self):
        po = tt.parse_option(opt_lot("-SOFI270115C30", "", 1))
        self.assertEqual(po["underlying"], "SOFI")
        self.assertEqual(po["type"], "call")          # C -> call
        self.assertEqual(po["strike"], 30.0)
        self.assertEqual(po["expiry"], dt.date(2027, 1, 15))
        # parity: every symbol security_key calls an option MUST parse (not None), incl leading-space/lowercase
        for sym in ("AAL 17 Call", "-SOFI270115C30", " -sofi270115c30", "AMZN 175 Put", "S 20 Call"):
            if tt.security_key(sym)["kind"] == "option":
                self.assertIsNotNone(tt.parse_option(opt_lot(sym, "Jul-17-2026", 1)), sym)

    def test_underlying_spots(self):
        lots = [stock_lot("AAL", 100, 1300), opt_lot("AAL 17 Call", "Jul-17-2026", 5)]
        spots = tt.underlying_spots(lots)
        self.assertAlmostEqual(spots["AAL"], 13.0)
        self.assertNotIn("AAL 17 CALL", spots)        # option is not a spot source

    def test_underlying_spots_uses_largest_value_lot(self):
        # per-share inconsistent across lots (export quirk) -> the largest-current-value lot wins
        lots = [stock_lot("AAL", 1, 74.0), stock_lot("AAL", 100, 1300.0)]   # $74/sh (val 74) vs $13/sh (val 1300)
        self.assertAlmostEqual(tt.underlying_spots(lots)["AAL"], 13.0)

    def test_moneyness_and_exposure(self):
        lots = [
            stock_lot("AAL", 100, 1300),                                       # spot 13
            opt_lot("AAL 17 Call", "Jul-17-2026", 5, current_value=1000, cost=800),  # OTM (13<17)
            opt_lot("AAL 10 Call", "Jul-17-2026", 2, current_value=600, cost=500),   # ITM (13>10)
        ]
        positions, by_u, s = tt.options_exposure(lots, self.AS_OF)
        m = {(p["underlying"], p["strike"]): p for p in positions}
        self.assertEqual(m[("AAL", 17.0)]["moneyness"], "OTM")
        self.assertEqual(m[("AAL", 10.0)]["moneyness"], "ITM")
        self.assertAlmostEqual(m[("AAL", 17.0)]["notional"], 17 * 100 * 5)
        self.assertAlmostEqual(m[("AAL", 17.0)]["premium"], 1000)
        self.assertFalse(s["has_short"])
        self.assertAlmostEqual(s["long_premium_at_risk"], 1600)
        self.assertAlmostEqual(s["bullish_notional"], 17 * 100 * 5 + 10 * 100 * 2)   # both long calls

    def test_put_bearish_and_no_spot(self):
        positions, by_u, s = tt.options_exposure(
            [opt_lot("USO 80 Put", "Aug-21-2026", 1, current_value=280, cost=250)], self.AS_OF)
        self.assertEqual(positions[0]["moneyness"], "n/a")      # no USO stock held
        self.assertAlmostEqual(s["bearish_notional"], 80 * 100 * 1)
        self.assertAlmostEqual(s["bullish_notional"], 0.0)

    def test_short_naked_call_and_assignment_cash(self):
        positions, by_u, s = tt.options_exposure([
            opt_lot("AAL 17 Call", "Jul-17-2026", -1, current_value=100, cost=0),   # written call, 0 shares held
            opt_lot("AAL 15 Put", "Jul-17-2026", -2, current_value=200, cost=0),    # written put
        ], self.AS_OF)
        self.assertTrue(s["has_short"])
        self.assertTrue(s["has_naked_calls"])
        self.assertAlmostEqual(s["total_put_assignment_cash"], 15 * 100 * 2)

    def test_expired_excluded_from_exposure(self):
        lots = [
            stock_lot("AAL", 100, 1300),
            opt_lot("AAL 10 Call", "Jul-17-2026", 2, current_value=600),   # live
            opt_lot("XYZ 100 Call", "Jan-16-2026", 3, current_value=300),  # expired before AS_OF
        ]
        positions, by_u, s = tt.options_exposure(lots, self.AS_OF)
        self.assertEqual(s["n_positions"], 1)                 # only the live option
        self.assertEqual(s["n_expired_excluded"], 1)
        self.assertNotIn("XYZ", [p["underlying"] for p in positions])
        self.assertAlmostEqual(s["bullish_notional"], 10 * 100 * 2)   # expired 100-strike call not counted

    def test_covered_call_same_account(self):
        positions, by_u, s = tt.options_exposure([
            stock_lot("XYZ", 100, 5000),                                          # 100 shares, same account
            opt_lot("XYZ 50 Call", "Jul-17-2026", -1, current_value=100, cost=0),  # short 1 call, same account
        ], self.AS_OF)
        self.assertFalse(s["has_naked_calls"])                                    # 100 shares cover 1 short call
        xyz = next(a for a in by_u if a["underlying"] == "XYZ")
        self.assertAlmostEqual(xyz["covered_contracts"], 1.0)

    def test_coverage_is_same_account(self):
        # shares in Account A do NOT cover a short call written in Account B (even unfiltered)
        lots = [
            stock_lot("XYZ", 100, 5000, account="Account A Test"),
            opt_lot("XYZ 50 Call", "Jul-17-2026", -1, current_value=100, cost=0, account="Account B Test"),
        ]
        self.assertTrue(tt.options_exposure(lots, self.AS_OF)[2]["has_naked_calls"])
        self.assertTrue(tt.options_exposure(lots, self.AS_OF, account="Account B")[2]["has_naked_calls"])
        self.assertEqual(tt.options_exposure(lots, self.AS_OF, account="Account A")[2]["n_positions"], 0)


class ExpirationTests(unittest.TestCase):
    AS_OF = dt.date(2026, 7, 1)

    def test_sorted_and_nearest(self):
        lots = [
            opt_lot("AAL 17 Call", "Aug-21-2026", 5, current_value=1000),
            opt_lot("AMZN 175 Put", "Jul-17-2026", 10, current_value=2000),
        ]
        rows, s = tt.expiration_calendar(lots, self.AS_OF)
        self.assertEqual([r["underlying"] for r in rows], ["AMZN", "AAL"])   # Jul before Aug
        self.assertEqual(s["nearest_expiry"], "2026-07-17")
        self.assertEqual(s["nearest_days"], (dt.date(2026, 7, 17) - self.AS_OF).days)
        self.assertAlmostEqual(s["total_premium_at_risk"], 3000)

    def test_within_filter(self):
        lots = [
            opt_lot("AAL 17 Call", "Jul-10-2026", 1, current_value=100),   # 9 days
            opt_lot("AAL 15 Call", "Dec-18-2026", 1, current_value=100),   # far
        ]
        rows, s = tt.expiration_calendar(lots, self.AS_OF, within=30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["expiry"], "2026-07-10")

    def test_short_put_assignment_cash(self):
        rows, s = tt.expiration_calendar(
            [opt_lot("AAL 15 Put", "Jul-17-2026", -2, current_value=200)], self.AS_OF)
        self.assertAlmostEqual(s["total_assignment_cash"], 15 * 100 * 2)
        self.assertAlmostEqual(s["total_premium_at_risk"], 0.0)            # short -> no long premium at risk

    def test_itm_and_expired(self):
        lots = [
            stock_lot("AAL", 100, 1300),                                   # spot 13
            opt_lot("AAL 10 Call", "Jul-17-2026", 1, current_value=300),   # ITM (13>10)
            opt_lot("AAL 17 Call", "Jun-01-2026", 1, current_value=50),    # already expired (< as_of)
        ]
        rows, s = tt.expiration_calendar(lots, self.AS_OF)
        self.assertGreaterEqual(s["n_itm"], 1)
        self.assertGreaterEqual(s["expired"], 1)
        self.assertLess(next(r for r in rows if r["expiry"] == "2026-06-01")["days"], 0)

    def test_missing_current_value_no_raise(self):
        rows, s = tt.expiration_calendar(
            [opt_lot("AAL 17 Call", "Jul-17-2026", 1, current_value=None)], self.AS_OF)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["premium"], 0.0)

    def test_expired_excluded_from_live_metrics(self):
        lots = [
            opt_lot("AAL 10 Call", "Jul-17-2026", 1, current_value=300),   # live, 16 days
            opt_lot("AAL 17 Call", "Jun-01-2026", 1, current_value=50),    # expired (-30 days)
        ]
        rows, s = tt.expiration_calendar(lots, self.AS_OF, within=30)
        self.assertEqual(len(rows), 2)                       # both still LISTED
        self.assertEqual(s["expired"], 1)
        self.assertEqual(s["n_expiring_soon"], 1)            # only the live one is "soon"
        self.assertEqual(s["nearest_expiry"], "2026-07-17")  # nearest NON-expired
        self.assertAlmostEqual(s["total_premium_at_risk"], 300)   # expired premium excluded

    def test_expired_short_put_assignment_cash_excluded(self):
        lots = [
            opt_lot("AAL 15 Put", "Jul-17-2026", -2, current_value=200),   # live short put
            opt_lot("AAL 15 Put", "Jun-01-2026", -3, current_value=300),   # expired short put
        ]
        _, s = tt.expiration_calendar(lots, self.AS_OF)
        self.assertAlmostEqual(s["total_assignment_cash"], 15 * 100 * 2)       # live only
        self.assertAlmostEqual(s["expired_assignment_cash"], 15 * 100 * 3)     # tracked separately


class Tier1ReproTests(unittest.TestCase):
    """Reproduce four actively-wrong-output bugs (crucible reproduce gate). Each asserts the FIXED
    behavior and currently FAILS. Synthetic data only."""
    AS_OF = dt.date(2026, 7, 1)

    def _lot(self, **k):
        d = dict(account="Individual - TOD Test", symbol="AAPL", quantity=10.0, current_value=2000.0,
                 gain_loss=1000.0, date_acquired="2024-01-01", term="Long-Term", cost_basis_total=1000.0,
                 avg_cost_basis=100.0, gain_loss_pct=100.0, description="", margin_cash="Margin")
        d.update(k)
        return d

    # Bug 1: sell/select_lots must never operate on tax-advantaged (Roth/IRA/HSA/...) lots.
    def test_bug1_select_lots_excludes_tax_advantaged(self):
        lots = [
            self._lot(account="Individual - TOD Test", quantity=10, current_value=2000, cost_basis_total=1000),
            self._lot(account="Roth IRA Test", quantity=10, current_value=2000, cost_basis_total=1000),
        ]
        picks, s = tt.select_lots(lots, "AAPL", 20, "min-tax", as_of=self.AS_OF)
        self.assertTrue(all(tt.is_taxable(p["account"]) for p in picks),
                        "select_lots must not pick tax-advantaged lots")
        self.assertEqual(s["available_shares"], 10.0)      # only the taxable lot is sellable
        self.assertAlmostEqual(s["est_tax"], 1000 * 0.15)  # tax only on the taxable Long-Term gain

    # Bug 2a: liquidation_estimate must net ST vs LT and never invent a negative tax from a net gain.
    def test_bug2_liquidation_nets_st_lt(self):
        lots = [
            self._lot(symbol="STL", date_acquired="2026-06-01", term="Short-Term",
                      gain_loss=-10000, current_value=0, cost_basis_total=10000),
            self._lot(symbol="LTG", date_acquired="2024-01-01", term="Long-Term",
                      gain_loss=10000, current_value=20000, cost_basis_total=10000),
        ]
        le = tt.liquidation_estimate(lots, self.AS_OF, st_rate=0.32, lt_rate=0.15)
        # ST loss fully nets the LT gain -> net 0 -> ~0 tax, NOT a negative "refund"
        self.assertGreaterEqual(le["est_tax"], 0.0)
        self.assertAlmostEqual(le["est_tax"], 0.0, places=2)

    # Bug 2b: harvest est_benefit must respect the $3k ordinary-offset cap when there are no gains.
    def test_bug2_harvest_benefit_capped(self):
        lots = [self._lot(symbol=f"L{i}", gain_loss=-50000.0, current_value=0,
                          cost_basis_total=50000, date_acquired="2024-01-01") for i in range(3)]
        _, s = tt.harvest(lots, self.AS_OF, st_rate=0.32, lt_rate=0.15)
        # -150k of losses, no offsetting gains -> first-year benefit is at most $3,000 * lt_rate, not $22,500
        self.assertLessEqual(s["est_benefit"], 3000 * 0.32 + 1e-6)

    # Bug 3: expired options (expiry < as_of) must not count as live exposure.
    def test_bug3_options_exclude_expired(self):
        lots = [opt_lot("XYZ 100 Call", "Jan-16-2026", 3, current_value=300)]  # expired before AS_OF
        positions, by_u, s = tt.options_exposure(lots, self.AS_OF)
        self.assertEqual(s["n_positions"], 0)
        self.assertAlmostEqual(s["bullish_notional"], 0.0)
        self.assertAlmostEqual(s["long_premium_at_risk"], 0.0)

    def test_bug3_expiration_soon_excludes_expired(self):
        lots = [opt_lot("XYZ 100 Call", "Jan-16-2026", 3, current_value=300)]  # -166 days
        _, s = tt.expiration_calendar(lots, self.AS_OF, within=30)
        self.assertEqual(s["n_expiring_soon"], 0)          # already expired is NOT "expiring soon"
        self.assertAlmostEqual(s["soon_premium_at_risk"], 0.0)

    # Bug 4: inconsistent per-share value across a symbol's lots must be flagged, not silently trusted.
    def test_bug4_price_dispersion_flagged(self):
        lots = [
            self._lot(symbol="AAPL", quantity=10, current_value=2000, cost_basis_total=1000),  # $200/sh
            self._lot(symbol="AAPL", quantity=10, current_value=100, cost_basis_total=1000),   # $10/sh (corrupt)
        ]
        flags = tt.price_dispersion_flags(lots)
        self.assertIn("AAPL", flags)


class PriceDispersionTests(unittest.TestCase):
    def _lot(self, symbol, qty, value):
        return lot(account="Individual - TOD Test", symbol=symbol, quantity=float(qty),
                   current_value=float(value), gain_loss=0.0, date_acquired="2024-01-01",
                   term="Long-Term", cost_basis_total=float(value), avg_cost_basis=1.0, gain_loss_pct=0.0)

    def test_consistent_prices_not_flagged(self):
        lots = [self._lot("AAPL", 10, 2000), self._lot("AAPL", 5, 1000)]   # both $200/sh
        self.assertEqual(tt.price_dispersion_flags(lots), {})

    def test_inconsistent_prices_flagged(self):
        lots = [self._lot("AAPL", 10, 2000), self._lot("AAPL", 10, 100)]   # $200 vs $10
        flags = tt.price_dispersion_flags(lots)
        self.assertIn("AAPL", flags)
        self.assertAlmostEqual(flags["AAPL"]["min"], 10.0)
        self.assertAlmostEqual(flags["AAPL"]["max"], 200.0)

    def test_single_lot_not_flagged(self):
        self.assertEqual(tt.price_dispersion_flags([self._lot("AAPL", 10, 2000)]), {})

    def test_options_and_cash_ignored(self):
        lots = [
            lot(account="A", symbol="AAA 20 Call", quantity=2, current_value=1600, description="Jul-17-2026",
                gain_loss=0.0, date_acquired="2026-06-01", term="Short-Term", cost_basis_total=1600,
                avg_cost_basis=8.0, gain_loss_pct=0.0),
            lot(account="A", symbol="AAA 20 Call", quantity=1, current_value=100, description="Jul-17-2026",
                gain_loss=0.0, date_acquired="2026-06-01", term="Short-Term", cost_basis_total=100,
                avg_cost_basis=1.0, gain_loss_pct=0.0),
        ]
        self.assertEqual(tt.price_dispersion_flags(lots), {})   # options are not price-checked

    def test_zero_price_no_crash(self):
        lots = [self._lot("AAPL", 10, 0), self._lot("AAPL", 5, 0)]   # all zero -> max==0, no flag, no crash
        self.assertEqual(tt.price_dispersion_flags(lots), {})


class Tier2ReproTests(unittest.TestCase):
    """Reproduce four remaining gaps (crucible reproduce gate). Each asserts the FIXED behavior and
    currently FAILS. Synthetic data only."""
    AS_OF = dt.date(2026, 7, 1)

    # Bug 5: holdings overview must recompute term from the acquisition date, not the stale stored term.
    def test_bug5_holdings_overview_recomputes_term(self):
        stale = lot(account="Individual - TOD Test", symbol="AAPL", quantity=10.0,
                    date_acquired="2024-01-01", term="Short-Term",   # stored stale; really Long-Term now
                    current_value=2000.0, cost_basis_total=1000.0, gain_loss=1000.0, gain_loss_pct=100.0)
        ov = tt.holdings_overview([stale], self.AS_OF)
        by_sym = {r["symbol"]: r for r in ov["by_symbol"]}
        self.assertAlmostEqual(by_sym["AAPL"]["long_units"], 10.0)
        self.assertAlmostEqual(by_sym["AAPL"]["short_units"], 0.0)

    def test_holdings_overview_cash_placement(self):
        # Cash belongs in by_symbol and in per-account market value, but NOT in the long/short term split.
        lots = [
            lot(account="A", symbol="AAA", quantity=10.0, current_value=1000.0,
                date_acquired="2024-01-01", term="Short-Term"),   # recomputes Long-Term as of AS_OF
            lot(account="A", symbol="CASH", quantity=None, current_value=500.0,
                date_acquired="", term=""),                        # cash: no acquisition, blank term
        ]
        ov = tt.holdings_overview(lots, self.AS_OF)
        syms = {r["symbol"]: r for r in ov["by_symbol"]}
        self.assertIn("CASH", syms)                                # cash shown in the per-symbol table
        self.assertEqual(syms["CASH"]["long_units"], 0.0)
        self.assertEqual(syms["CASH"]["short_units"], 0.0)
        self.assertAlmostEqual(sum(r["market_value"] for r in ov["term_totals"]), 1000.0)   # excludes cash
        self.assertAlmostEqual(sum(r["market_value"] for r in ov["by_account"]), 1500.0)    # includes cash

    # Bug 6a: options must be excluded from the equity value-concentration ranking.
    def test_bug6_concentration_excludes_options(self):
        lots = [
            lot(account="A", symbol="AAPL", current_value=10000.0),
            lot(account="A", symbol="AAPL 250 Call", current_value=1000.0, description="Jul-17-2026"),
        ]
        rows, s = tt.concentration(lots)
        self.assertEqual([r["symbol"] for r in rows], ["AAPL"])   # the option is not a separate name
        self.assertEqual(s["n_options_excluded"], 1)

    # Bug 6b: a non-positive (short/corrupt) value must not collapse the whole report.
    def test_bug6_concentration_negative_value_does_not_collapse(self):
        lots = [
            lot(account="A", symbol="AAPL", current_value=1000.0),
            lot(account="A", symbol="BADSTK", current_value=-5000.0),   # corrupt/negative
        ]
        rows, s = tt.concentration(lots)
        self.assertIn("AAPL", [r["symbol"] for r in rows])          # real position still shown

    def test_bug6_excluded_counts_reported(self):
        # Both exclusions are counted and the negative symbol is dropped from the ranking.
        lots = [
            lot(account="A", symbol="AAPL", current_value=1000.0),
            lot(account="A", symbol="AAPL 250 Call", current_value=300.0, description="Jul-17-2026"),
            lot(account="A", symbol="SHORTY", current_value=-200.0),
        ]
        rows, s = tt.concentration(lots)
        self.assertEqual([r["symbol"] for r in rows], ["AAPL"])
        self.assertEqual(s["n_options_excluded"], 1)
        self.assertEqual(s["n_nonpositive_excluded"], 1)
        self.assertTrue(all(r["is_option"] is False for r in rows))   # is_option kept but always False

    # Bug 7: a zero-quantity closed lot is not a live position.
    def test_bug7_zero_qty_not_harvestable(self):
        z = lot(account="Individual - TOD Test", symbol="CLOSED", quantity=0.0, current_value=0.0,
                gain_loss=-123.0, date_acquired="2026-06-01", term="Short-Term", cost_basis_total=123.0)
        self.assertEqual(tt.taxable_loss_candidates([z]), [])
        self.assertEqual(tt.liquidation_estimate([z], self.AS_OF)["n_lots"], 0)

    def test_bug7_non_live_quantities_excluded_everywhere(self):
        # Zero, negative, non-numeric, and missing quantities are all non-live and excluded from every
        # live-position analysis.
        for q in (0.0, -5.0, "n/a", None):
            z = lot(account="Individual - TOD Test", symbol="X", quantity=q, current_value=100.0,
                    gain_loss=-50.0, date_acquired="2026-06-01", term="Short-Term", cost_basis_total=150.0)
            self.assertEqual(tt.taxable_loss_candidates([z]), [], q)
            self.assertEqual(tt.liquidation_estimate([z], self.AS_OF)["n_lots"], 0, q)
            self.assertEqual(tt.unrealized_by_account([z], self.AS_OF)[0], [], q)
            self.assertEqual(tt.ripening([z], self.AS_OF)[0], [], q)              # short-term loss can't ripen
            gain = lot(account="Individual - TOD Test", symbol="G", quantity=q, current_value=2000.0,
                       gain_loss=500.0, date_acquired="2024-01-01", term="Long-Term",
                       cost_basis_total=1500.0, gain_loss_pct=33.3)
            self.assertEqual(tt.gift_candidates([gain], self.AS_OF)[0], [], q)    # not a live donation lot

    def test_bug7_live_positive_quantity_still_counted(self):
        # A normal live lot is still counted by every analysis (the guard must not over-exclude).
        live = lot(account="Individual - TOD Test", symbol="Y", quantity=10.0, current_value=100.0,
                   gain_loss=-50.0, date_acquired="2026-06-01", term="Short-Term", cost_basis_total=150.0)
        self.assertEqual(len(tt.taxable_loss_candidates([live])), 1)
        self.assertEqual(tt.liquidation_estimate([live], self.AS_OF)["n_lots"], 1)
        self.assertEqual(len(tt.unrealized_by_account([live], self.AS_OF)[0]), 1)
        self.assertEqual(len(tt.ripening([live], self.AS_OF)[0]), 1)
        gain = lot(account="Individual - TOD Test", symbol="G", quantity=3.0, current_value=2000.0,
                   gain_loss=500.0, date_acquired="2024-01-01", term="Long-Term",
                   cost_basis_total=1500.0, gain_loss_pct=33.3)
        self.assertEqual(len(tt.gift_candidates([gain], self.AS_OF)[0]), 1)


if __name__ == "__main__":
    unittest.main()
