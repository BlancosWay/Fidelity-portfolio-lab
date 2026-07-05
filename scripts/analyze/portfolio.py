#!/usr/bin/env python3
"""Fidelity-portfolio-lab analyzer.

Load an exported Fidelity lots CSV into a local SQLite table and analyze it. Stdlib only.

Holding term is RECOMPUTED authoritatively from each lot's acquisition date (the CSV's preview
"Term (>1yr rule)" column is ignored): a lot is Long-Term iff `as_of` is strictly after its
one-year calendar anniversary, else Short-Term. A Feb-29 acquisition clamps to Feb-28 of the next
year, so Mar-1 is its first long-term day. Dates are compared calendar-day to calendar-day.

Usage:
  python portfolio.py load <lots.csv> [--db DB] [--as-of YYYY-MM-DD]
  python portfolio.py summary [--db DB]
  python portfolio.py symbol <SYMBOL> [--db DB]
  python portfolio.py accounts [--db DB]
  python portfolio.py query "SELECT ... FROM lots ..." [--db DB]   # read-only
"""
import argparse
import csv
import datetime as dt
import os
import re
import sqlite3
import sys
from urllib.request import pathname2url

from common import (  # noqa: F401  (re-exported so portfolio.<name> keeps working)
    MONTHS, parse_money, parse_qty, parse_date, parse_us_date,
    one_year_anniversary, holding_term,
)
import tax_tools
import history

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "portfolio.db")

# Exact export schema shared with scripts/browser/fidelity_lot_export.js.
EXPECTED_HEADERS = [
    "Account", "Symbol", "Description", "Margin/Cash", "Quantity", "Date Acquired",
    "Term (>1yr rule)", "Term (Fidelity)", "Average Cost Basis", "Cost Basis Total",
    "Current Value", "Gain/Loss $", "Gain/Loss %",
]

# (db_column, csv_header, kind).  "Term (>1yr rule)" is intentionally not stored: we recompute it.
COLUMNS = [
    ("account", "Account", "text"),
    ("symbol", "Symbol", "text"),
    ("description", "Description", "text"),
    ("margin_cash", "Margin/Cash", "text"),
    ("quantity", "Quantity", "qty"),
    ("date_acquired", "Date Acquired", "date"),
    ("term_fidelity", "Term (Fidelity)", "text"),
    ("avg_cost_basis", "Average Cost Basis", "money"),
    ("cost_basis_total", "Cost Basis Total", "money"),
    ("current_value", "Current Value", "money"),
    ("gain_loss", "Gain/Loss $", "money"),
    ("gain_loss_pct", "Gain/Loss %", "money"),
]


# --------------------------------------------------------------------------- parsing
# parse_money / parse_qty / parse_date / parse_us_date / one_year_anniversary / holding_term / MONTHS
# now live in common.py and are re-imported above (kept as portfolio.<name> for backward compat).




# --------------------------------------------------------------------------- db helpers
def _sql_type(kind):
    return "REAL" if kind in ("money", "qty") else "TEXT"


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def readonly_connection(db_path):
    """Open the DB strictly read-only (immutable to writes even before validation)."""
    uri = "file:" + pathname2url(os.path.abspath(db_path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def fetch_lots(db_path=DEFAULT_DB):
    """Read every lot row as a list of plain dicts via a strictly read-only connection.

    The analysis subcommands (harvest/washsale/sell/ripening/concentration) call this; only ``load``
    ever writes. Returns dicts keyed by the ``lots`` table columns (account, symbol, quantity,
    date_acquired, term, cost_basis_total, current_value, gain_loss, ...)."""
    conn = readonly_connection(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM lots").fetchall()]
    finally:
        conn.close()


def _no_portfolio_hint(db_path):
    """Print a friendly 'run load first' message (used when the DB is missing/unloaded)."""
    print(f"No portfolio loaded at {db_path}.\n"
          f"  Run: python scripts/analyze/portfolio.py --db {db_path} load <lots.csv>")


def read_lots(db_path=DEFAULT_DB):
    """Read lots strictly read-only, or print a friendly hint and return None when the DB is missing or
    has never been loaded. Never creates the DB file (unlike a read-write connect). Every read-only
    report/analysis command routes through this so a missing DB yields a hint, not a traceback."""
    try:
        return fetch_lots(db_path)
    except sqlite3.OperationalError:
        # missing file ("unable to open database file") or never loaded ("no such table: lots")
        _no_portfolio_hint(db_path)
        return None


# --------------------------------------------------------------------------- load
def load(csv_path, db_path=DEFAULT_DB, as_of=None):
    as_of = as_of or dt.date.today()
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        if headers != EXPECTED_HEADERS:
            raise ValueError(
                "CSV headers do not match the expected export schema.\n"
                f"  expected: {EXPECTED_HEADERS}\n  got:      {headers}")
        rows = list(reader)

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS lots")
    coldefs = ", ".join(f"{c} {_sql_type(k)}" for c, _, k in COLUMNS) + ", term TEXT"
    cur.execute(f"CREATE TABLE lots ({coldefs})")

    insert_cols = [c for c, _, _ in COLUMNS] + ["term"]
    placeholders = ", ".join("?" for _ in insert_cols)
    parsers = {"money": parse_money, "qty": parse_qty}
    n = 0
    for r in rows:
        vals, acquired = [], None
        for _, header, kind in COLUMNS:
            raw = r.get(header)
            if kind == "date":
                acquired = parse_date(raw)
                vals.append(acquired.isoformat() if acquired else None)
            elif kind in parsers:
                vals.append(parsers[kind](raw))
            else:
                vals.append((raw or "").strip())
        vals.append(holding_term(acquired, as_of))
        cur.execute(f"INSERT INTO lots ({', '.join(insert_cols)}) VALUES ({placeholders})", vals)
        n += 1
    conn.commit()
    conn.close()
    return n


# --------------------------------------------------------------------------- read-only query
def _validate_query(sql):
    stmt = (sql or "").strip()
    if stmt.endswith(";"):
        stmt = stmt[:-1].strip()
    if not stmt:
        raise ValueError("empty query")
    if ";" in stmt:
        raise ValueError("only a single statement is allowed")
    low = stmt.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise ValueError("only read-only SELECT/WITH queries are allowed")
    for kw in ("attach", "detach", "pragma", "insert", "update", "delete", "drop",
               "alter", "create", "replace", "reindex", "vacuum", "begin", "commit"):
        if re.search(rf"\b{kw}\b", low):
            raise ValueError(f"disallowed keyword in read-only query: {kw}")
    return stmt


def run_query(db_path, sql):
    stmt = _validate_query(sql)
    try:
        conn = readonly_connection(db_path)
    except sqlite3.OperationalError:
        _no_portfolio_hint(db_path)
        return None
    try:
        return conn.execute(stmt).fetchall()
    except sqlite3.OperationalError as e:
        if str(e).lower() == "no such table: lots":
            _no_portfolio_hint(db_path)   # DB file exists but was never loaded
        else:
            print(f"Query error: {e}")    # genuine SQL error (bad column/other table/etc.)
        return None
    finally:
        conn.close()


# --------------------------------------------------------------------------- reports
def _print_table(headers, rows):
    data = [["" if v is None else str(v) for v in r] for r in rows]
    widths = [max([len(str(headers[i]))] + [len(r[i]) for r in data]) for i in range(len(headers))]
    print("  ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in data:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(headers))))


def summary(db_path, as_of=None):
    lots = read_lots(db_path)
    if lots is None:
        return
    as_of = as_of or dt.date.today()
    ov = tax_tools.holdings_overview(lots, as_of)
    print("== Units per symbol across ALL accounts ==")
    _print_table(["Symbol", "Units", "Lots", "#Accts", "Long(>1yr)", "Short(<=1yr)"],
                 [(r["symbol"], r["units"], r["lots"], r["accts"], r["long_units"], r["short_units"])
                  for r in ov["by_symbol"]])

    print("\n== Long vs Short (whole portfolio) ==")
    _print_table(["Term", "Lots", "Market Value"],
                 [(r["term"], r["lots"], r["market_value"]) for r in ov["term_totals"]])

    print("\n== Per account by term ==")
    _print_table(["Account", "Long lots", "Short lots", "Market Value"],
                 [(r["account"], r["long_lots"], r["short_lots"], r["market_value"]) for r in ov["by_account"]])


def symbol_detail(db_path, sym, as_of=None):
    lots = read_lots(db_path)
    if lots is None:
        return
    as_of = as_of or dt.date.today()
    matched = [lot for lot in lots if (lot.get("symbol") or "") == sym]
    if not matched:
        print(f"No lots for symbol {sym!r}")
        return

    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    rows, units, long_units, short_units = [], 0.0, 0.0, 0.0
    for lot in sorted(matched, key=lambda l: (l.get("date_acquired") or "")):
        term = tax_tools.recompute_term(lot, as_of)
        qty = _num(lot.get("quantity"))
        rows.append((lot.get("account"), lot.get("quantity"), lot.get("date_acquired"),
                     term, lot.get("current_value")))
        units += qty
        if term == "Long-Term":
            long_units += qty
        elif term == "Short-Term":
            short_units += qty
    _print_table(["Account", "Quantity", "Acquired", "Term", "Current Value"], rows)
    print(f"\nTotal {sym}: {round(units, 4)} units ({round(long_units, 4)} long, {round(short_units, 4)} short)")


def accounts_list(db_path):
    lots = read_lots(db_path)
    if lots is None:
        return
    agg = {}
    for lot in lots:
        acct = lot.get("account")
        a = agg.setdefault(acct, {"lots": 0, "symbols": set()})
        a["lots"] += 1
        a["symbols"].add((lot.get("symbol") or "").strip())
    rows = [(acct, a["lots"], len(a["symbols"])) for acct, a in sorted(agg.items(), key=lambda kv: kv[0] or "")]
    _print_table(["Account", "Lots", "Symbols"], rows)


# --------------------------------------------------------------------------- CLI
def _as_of(val):
    """Parse a --as-of YYYY-MM-DD (default today)."""
    return dt.date.fromisoformat(val) if val else dt.date.today()


def cmd_harvest(db_path, as_of, st_rate, lt_rate, offsetting_st_gains=0.0, offsetting_lt_gains=0.0,
                max_ordinary_offset=3000.0):
    lots = read_lots(db_path)
    if lots is None:
        return
    rows, s = tax_tools.harvest(lots, as_of, st_rate, lt_rate, offsetting_st_gains, offsetting_lt_gains,
                                max_ordinary_offset)
    if not rows:
        print("No harvestable losses in taxable accounts.")
        return
    _print_table(
        ["Account", "Symbol", "Term", "Qty", "Cost Basis", "Current Value", "Loss $", "Loss %"],
        [(r["account"], r["symbol"], r["term"], r["quantity"], r["cost_basis_total"],
          r["current_value"], round(r["loss"], 2), r["loss_pct"]) for r in rows],
    )
    print(f"\nHarvestable losses (taxable accounts, as of {as_of}):")
    print(f"  Short-Term: {s['st_lots']} lots, ${s['st_loss']:,.2f}")
    print(f"  Long-Term:  {s['lt_lots']} lots, ${s['lt_loss']:,.2f}")
    print(f"  Estimated current-year tax benefit: ~${s['est_benefit']:,.2f}  [estimate, not tax advice]")
    print(f"  (harvested losses first offset realized gains of the same character, then up to "
          f"${max_ordinary_offset:,.0f} of ordinary income per year; the rest carries forward)")
    if s["carryforward_loss"] > 0:
        print(f"  Loss carried forward to future years: ${s['carryforward_loss']:,.2f}")
    flags = tax_tools.price_dispersion_flags(lots)
    flagged = sorted({r["symbol"] for r in rows if (r["symbol"] or "").strip().upper() in flags})
    if flagged:
        print(f"  WARNING: inconsistent per-share prices for: {', '.join(flagged)}; "
              "the export may be corrupted -- verify these lots before harvesting.")
    if s["has_options"]:
        print("  Note: includes option lots -- verify your own tax treatment for options.")


def cmd_ripening(db_path, as_of, within, st_rate, lt_rate):
    lots = read_lots(db_path)
    if lots is None:
        return
    rows, s = tax_tools.ripening(lots, as_of, st_rate, lt_rate, within)
    if not rows:
        print("No taxable short-term lots ripening" + (f" within {within} days." if within else "."))
        return
    _print_table(
        ["Account", "Symbol", "Acquired", "Ripens", "Days", "G/L $", "Hint"],
        [(r["account"], r["symbol"], r["acquired"], r["ripens_on"], r["days_until"],
          round(r["gain_loss"], 2), r["hint"]) for r in rows],
    )
    print(f"\nRipening (taxable short-term lots, as of {as_of}): {s['count']} lots "
          f"({s['winners']} winners, {s['losers']} losers).")
    print(f"  Est. tax saved by waiting for long-term on winners (ST@{st_rate:.0%} vs LT@{lt_rate:.0%}): "
          f"~${s['total_tax_saved_by_waiting']:,.2f}  [estimate, not tax advice]")


def cmd_concentration(db_path, top, threshold):
    lots = read_lots(db_path)
    if lots is None:
        return
    rows, s = tax_tools.concentration(lots, top, threshold)

    def _exclusion_notes():
        if s.get("n_options_excluded"):
            print(f"  NOTE: {s['n_options_excluded']} option lot(s) excluded from the equity ranking "
                  "(premium is not notional exposure -- see the `options` command).")
        if s.get("n_nonpositive_excluded"):
            print(f"  NOTE: {s['n_nonpositive_excluded']} symbol(s) with non-positive value excluded "
                  "(short position or corrupt export value).")

    if not rows:
        print(f"No non-cash equity positions. Cash: ${s['cash_total']:,.2f} (100%).")
        _exclusion_notes()
        return
    _print_table(
        ["Symbol", "Value", "% Inv", "Cum %", "#Acct", "Flag"],
        [(r["symbol"], round(r["value"], 2), f"{r['weight'] * 100:.2f}%", f"{r['cumulative'] * 100:.2f}%",
          r["accounts"], (">%.0f%%" % (threshold * 100)) if r["over_threshold"] else "") for r in rows[:top]],
    )
    eff = f"{s['effective_positions']:.1f}" if s["effective_positions"] is not None else "N/A"
    print(f"\nInvested (non-cash): ${s['invested_total']:,.2f}; cash ${s['cash_total']:,.2f} "
          f"({s['cash_pct'] * 100:.1f}% of ${s['total']:,.2f} total).")
    print(f"  {s['num_positions']} positions; HHI={s['hhi']:.4f}; effective positions={eff}.")
    if s["over_threshold"]:
        print(f"  Over {threshold * 100:.0f}% single-name concentration: {', '.join(s['over_threshold'])}")
    _exclusion_notes()


def cmd_sell(db_path, symbol, shares, account, strategy, as_of, st_rate, lt_rate,
             max_ordinary_offset=3000.0):
    lots = read_lots(db_path)
    if lots is None:
        return
    picks, s = tax_tools.select_lots(lots, symbol, shares, strategy, account, as_of, st_rate, lt_rate,
                                     max_ordinary_offset)
    if not picks:
        print(f"No sellable taxable lots found for {s['symbol']}"
              + (f" in accounts matching '{account}'." if account else " (tax-advantaged lots are excluded)."))
        return
    _print_table(
        ["Account", "Acquired", "Term", "Qty", "Basis", "Est Proceeds", "Realized G/L"],
        [(p["account"], p["acquired"], p["term"], round(p["qty_used"], 4), round(p["basis"], 2),
          round(p["proceeds"], 2), round(p["realized_gain"], 2)) for p in picks],
    )
    print(f"\n{s['strategy']} sale of {s['filled_shares']:g}/{s['requested_shares']:g} sh {s['symbol']} (as of {as_of}):")
    if s["insufficient"]:
        print(f"  WARNING: only {s['available_shares']:g} taxable shares available; short by "
              f"{s['requested_shares'] - s['filled_shares']:g}.")
    if s["multi_account"]:
        print("  NOTE: picks span multiple accounts; specific-ID sales are per-account -- "
              "place one order per account.")
    disp = tax_tools.price_dispersion_flags(lots).get(s["symbol"])
    if disp:
        print(f"  WARNING: {s['symbol']} lots show inconsistent per-share prices "
              f"(${disp['min']:,.2f}..${disp['max']:,.2f}); the export may be corrupted -- verify before acting.")
    print(f"  Realized: ST ${s['st_gain']:,.2f} + LT ${s['lt_gain']:,.2f} = ${s['realized_gain']:,.2f}")
    print(f"  vs FIFO ${s['fifo_realized_gain']:,.2f}  (delta ${s['delta_vs_fifo']:,.2f}; negative = less gain realized)")
    if s["net_loss"] > 0:
        print(f"  Est. tax (ST@{st_rate:.0%}, LT@{lt_rate:.0%}, ST/LT netted): ~${s['est_tax']:,.2f}  "
              f"(net capital LOSS ${s['net_loss']:,.2f}: ${s['deductible_loss']:,.2f} offsets ordinary income now, "
              f"${s['carryforward']:,.2f} carries forward)  [estimate, not tax advice]")
    else:
        print(f"  Est. tax (ST@{st_rate:.0%}, LT@{lt_rate:.0%}, ST/LT netted): ~${s['est_tax']:,.2f}  "
              f"[estimate, not tax advice]")


def cmd_washsale(db_path, history_path, as_of, window, same_underlying):
    lots = read_lots(db_path)
    if lots is None:
        return
    candidates = tax_tools.taxable_loss_candidates(lots)
    res = tax_tools.washsale(candidates, history.load_history(history_path), as_of, window, same_underlying)
    c, s = res["candidates"], res["summary"]
    if c:
        _print_table(
            ["Account", "Symbol", "Loss $", "Status", "Triggering purchases (any account)"],
            [(r["account"], r["symbol"], round(r["loss"], 2) if r["loss"] is not None else "", r["status"],
              "; ".join(f"{t['action']}{'*' if t.get('inferred') else ''} {t['qty']:g} in {t['account']} {t['date']}"
                        for t in r["triggers"]) or "-")
             for r in c],
        )
    else:
        print("No taxable loss candidates to check.")
    print(f"\nWash-sale check (as of {as_of}, +/-{window}-day window):")
    print(f"  BLOCKED (replacement buy in an IRA/Roth/HSA -> loss permanently disallowed, e.g. Rev. Rul. 2008-5 for IRAs): {s['blocked']}")
    print(f"  CAUTION (replacement buy in a taxable account): {s['caution']}")
    print(f"  REVIEW  (replacement buy in a 401(k)/403(b)/BrokerageLink/529 -> wash-sale treatment unsettled; prevailing view is it does NOT apply, confirm with a tax pro): {s['review']}")
    print(f"  CLEAN: {s['clean']}")
    print("  * = INFERRED acquisition (option assignment/exercise or an inbound transfer/exchange); its"
          " status is capped at REVIEW -- verify whether it re-acquired a substantially identical position.")
    print(f"  Also DO NOT repurchase a harvested security within {window} days AFTER selling it.")
    if res["realized"]:
        print(f"\n  Realized-history review -- {len(res['realized'])} past sale(s) had a same-security "
              "purchase nearby (confirm whether the sale was at a loss):")
        for r in res["realized"]:
            buys = "; ".join(f"{m['action']} {m['qty']:g} in {m['account']} {m['date']}" for m in r["matches"])
            print(f"    {r['symbol']} sold {r['date']} in {r['account']}: {buys}")
    hs, he = s["history_start"], s["history_end"]
    if hs and he:
        print(f"\n  NOTE: history covers {hs.isoformat()}..{he.isoformat()}; purchases/reinvestments before "
              "that window are invisible, so CLEAN is not a guarantee.")
    print("  [informational, not tax advice]")


def cmd_capacity(db_path, income, ceiling, ceiling_label, target_gain, account, as_of, lt_rate, within_rate):
    lots = read_lots(db_path)
    if lots is None:
        return
    picks, s = tax_tools.gain_capacity(lots, as_of, income, ceiling, target_gain,
                                       account, lt_rate, within_rate)
    if s["n_candidates"] == 0:
        print("No taxable long-term gain lots to realize.")
        return
    if picks:
        _print_table(
            ["Account", "Symbol", "Acquired", "Qty", "Basis", "Value", "Gain $", "Gain %", "Part"],
            [(p["account"], p["symbol"], p["acquired"], round(p["qty_used"], 4),
              round(p["basis_used"], 2) if p["basis_used"] is not None else "",
              round(p["value_used"], 2) if p["value_used"] is not None else "",
              round(p["gain_used"], 2),
              f"{p['gain_pct']:.2f}%" if p["gain_pct"] is not None else "n/a",
              "PARTIAL" if p["partial"] else "") for p in picks],
        )
    if s["source"] == "inventory-only":
        print(f"\nLong-term gain inventory (taxable, as of {as_of}): ${s['available_gain']:,.2f} "
              f"across {s['n_candidates']} lots.")
        print("  Pass --income and --ceiling (e.g. the 0% LTCG bracket top) or --target-gain to plan a realization.")
    elif s["source"] == "headroom":
        print(f"\n{ceiling_label} headroom = ceiling ${s['ceiling']:,.2f} - income ${s['income']:,.2f} "
              f"= ${s['headroom']:,.2f}.")
        if s["above_ceiling"]:
            print("  Income is at/above the ceiling: $0 headroom.")
        print(f"  Realizing ${s['realized']:,.2f} of long-term gain fills it (constrained by "
              f"{s['constrained_by']}); leftover LT gain not realized: ${s['leftover_gain']:,.2f}.")
        print(f"  Est. tax on the realized gain (LT@{within_rate:.0%} below the ceiling): ~${s['est_tax']:,.2f}.")
        print("  (A 0% LTCG ceiling makes this gain tax-free; an NIIT/IRMAA ceiling only avoids that "
              "surcharge/tier -- the gain is still taxed at your LTCG rate, so pass --within-rate.)  "
              "[estimate, not tax advice]")
    else:  # target-gain
        print(f"\nTarget realized gain ${s['budget']:,.2f}: realized ${s['realized']:,.2f} "
              f"(constrained by {s['constrained_by']}).")
        print(f"  Est. tax on realized (LT@{lt_rate:.0%}): ~${s['est_tax']:,.2f}  [estimate, not tax advice]")
    print("  Note: realized gains raise MAGI and can affect NIIT/IRMAA; ceilings are the values you "
          "supply for your tax year.")


def cmd_gift(db_path, min_gain_pct, top, account, as_of, lt_rate):
    lots = read_lots(db_path)
    if lots is None:
        return
    rows, s = tax_tools.gift_candidates(lots, as_of, min_gain_pct, account, lt_rate)
    if rows:
        _print_table(
            ["Account", "Symbol", "Acquired", "Qty", "Basis", "Value", "Gain $", "Gain %", "Est Tax Avoided"],
            [(r["account"], r["symbol"], r["acquired"], r["quantity"],
              round(r["basis"], 2) if r["basis"] is not None else "",
              round(r["value"], 2) if r["value"] is not None else "",
              round(r["gain"], 2),
              f"{r['gain_pct']:.2f}%" if r["gain_pct"] is not None else "n/a",
              round(r["tax_avoided"], 2)) for r in rows[:top]],
        )
        print(f"\nDonation candidates (taxable long-term gains, as of {as_of}): {s['n_candidates']}.")
        print(f"  Donatable FMV: ${s['total_fmv']:,.2f}; unrealized LT gain: ${s['total_gain']:,.2f}; "
              f"est. cap-gains tax avoided if donated (LT@{lt_rate:.0%}): ~${s['total_tax_avoided']:,.2f}.")
    else:
        print(f"No taxable long-term appreciated lots at/above the gain threshold (as of {as_of}).")
    print(f"  {s['n_short_term_gain']} short-term gain lot(s) -> wait for long-term before donating; "
          f"{s['n_loss']} loss lot(s) -> sell to harvest instead (see 'harvest'/'sell').")
    print("  [estimate, not tax advice -- FMV deduction depends on itemizing and AGI limits.]")


def cmd_dashboard(db_path, as_of, st_rate, lt_rate, within, income, ceiling, max_ordinary_offset=3000.0):
    lots = read_lots(db_path)
    if lots is None:
        return
    print(f"===== Year-end tax dashboard (as of {as_of}) =====")

    rows, us = tax_tools.unrealized_by_account(lots, as_of)
    print("\n-- Unrealized gain/loss by account --")
    if rows:
        _print_table(
            ["Account", "Tax", "ST G/L", "LT G/L", "Total", "Mkt Value"],
            [(r["account"], "taxable" if r["taxable"] else "advantaged",
              round(r["st_gl"], 2), round(r["lt_gl"], 2), round(r["total_gl"], 2),
              round(r["market_value"], 2)) for r in rows],
        )
        print(f"  Taxable: ST ${us['taxable_st']:,.2f} + LT ${us['taxable_lt']:,.2f}; "
              f"tax-advantaged: ST ${us['adv_st']:,.2f} + LT ${us['adv_lt']:,.2f}.")
    else:
        print("  (no non-cash positions)")

    _, hs = tax_tools.harvest(lots, as_of, st_rate, lt_rate, max_ordinary_offset=max_ordinary_offset)
    print("\n-- Harvestable losses (taxable) --")
    print(f"  Short-Term: {hs['st_lots']} lots, ${hs['st_loss']:,.2f}; "
          f"Long-Term: {hs['lt_lots']} lots, ${hs['lt_loss']:,.2f}; est. benefit ~${hs['est_benefit']:,.2f}.")

    _, rs = tax_tools.ripening(lots, as_of, st_rate, lt_rate, within)
    print(f"\n-- Ripening within {within} days --")
    print(f"  {rs['count']} lots ({rs['winners']} winners, {rs['losers']} losers); "
          f"est. tax saved by waiting ~${rs['total_tax_saved_by_waiting']:,.2f}.")

    le = tax_tools.liquidation_estimate(lots, as_of, st_rate, lt_rate, max_ordinary_offset)
    print("\n-- If sold now (taxable liquidation estimate) --")
    print(f"  ST {le['st_gain']:+,.2f} + LT {le['lt_gain']:+,.2f} = net {le['total_gain']:+,.2f} "
          f"(ST@{st_rate:.0%}, LT@{lt_rate:.0%}, ST and LT netted).")
    if le["net_loss"] > 0:
        print(f"  Net capital LOSS: est. current-year benefit ~${-le['est_tax']:,.2f} "
              f"(${le['deductible_loss']:,.2f} offsets ordinary income; ${le['carryforward']:,.2f} carries forward).")
    else:
        print(f"  Est. tax on the net gain: ~${le['est_tax']:,.2f}.")

    print("\n-- 0% LTCG capacity --")
    if income is not None and ceiling is not None:
        _, cs = tax_tools.gain_capacity(lots, as_of, income=income, ceiling=ceiling)
        print(f"  Headroom to ${ceiling:,.2f} at income ${income:,.2f}: ${cs['headroom']:,.2f}; "
              f"realizable LT gain within it: ${cs['realized']:,.2f} (available ${cs['available_gain']:,.2f}).")
    else:
        print("  Pass --income and --ceiling to show 0% LTCG capacity.")

    print("\n[estimates, not tax advice]")


def cmd_options(db_path, as_of, account, top):
    lots = read_lots(db_path)
    if lots is None:
        return
    positions, by_u, s = tax_tools.options_exposure(lots, as_of, account)
    if not positions:
        msg = "No live option positions found."
        if s.get("n_expired_excluded"):
            msg += f" ({s['n_expired_excluded']} expired option lot(s) excluded.)"
        print(msg)
        return
    print("== Exposure by underlying ==")
    _print_table(
        ["Underlying", "Spot", "Calls(c)", "Puts(c)", "Premium $", "Notional $", "Bias", "Cover"],
        [(a["underlying"],
          f"{a['spot']:,.2f}" if a["spot"] is not None else "n/a",
          f"{a['long_call_contracts'] - a['short_call_contracts']:g}",
          f"{a['long_put_contracts'] - a['short_put_contracts']:g}",
          round(a["premium"], 2), round(a["notional"], 2), a["bias"],
          (f"{a['covered_contracts']:g}/{a['short_call_contracts']:g} cov"
           if a["short_call_contracts"] > 0 else "")) for a in by_u[:top]],
    )
    print("\n== Top positions by notional ==")
    _print_table(
        ["Account", "Option", "Exp", "Days", "Contracts", "Premium $", "Notional $", "Money"],
        [(p["account"], f"{p['underlying']} {p['strike']:g} {p['type'].upper()}", p["expiry"],
          p["days_to_expiry"] if p["days_to_expiry"] is not None else "",
          f"{p['contracts']:g}", round(p["premium"], 2), round(p["notional"], 2), p["moneyness"])
         for p in positions[:top]],
    )
    print(f"\nOption positions (as of {as_of}): {s['n_positions']} across {s['n_underlyings']} underlyings.")
    if s.get("n_expired_excluded"):
        print(f"  ({s['n_expired_excluded']} expired option lot(s) excluded from exposure.)")
    print(f"  Premium at risk (long): ${s['long_premium_at_risk']:,.2f}; notional exposure: "
          f"${s['total_notional']:,.2f} (bullish ${s['bullish_notional']:,.2f} / bearish ${s['bearish_notional']:,.2f}).")
    if s["has_short"]:
        print(f"  Short credit: ${s['short_credit']:,.2f}; put-assignment cash if assigned: "
              f"${s['total_put_assignment_cash']:,.2f}.")
        if s["has_naked_calls"]:
            print("  WARNING: naked short calls present (short calls beyond held shares).")
    print("  Notional = strike x 100 x contracts (multiplier 100; adjusted options may differ). "
          "ITM/OTM uses a spot from your largest held stock lot per underlying (approximate -- verify "
          "against your broker). Delta/theta need live quotes (not computed). [informational, not investment advice]")


def cmd_expiration(db_path, as_of, within, account, top):
    lots = read_lots(db_path)
    if lots is None:
        return
    rows, s = tax_tools.expiration_calendar(lots, as_of, within, account)
    if not rows:
        suffix = f" within {within} days" if within is not None else ""
        print(f"No dated option positions{suffix}.")
        return
    _print_table(
        ["Exp", "Days", "Account", "Option", "Contracts", "AtRisk $", "Money", "AssignCash $"],
        [(r["expiry"], r["days"], r["account"],
          f"{r['underlying']} {r['strike']:g} {r['type'].upper()}", f"{r['contracts']:g}",
          round(r["premium_at_risk"], 2), r["moneyness"],
          round(r["assignment_cash"], 2) if r["assignment_cash"] else "") for r in rows[:top]],
    )
    win = within if within is not None else 30
    winphrase = f" within {within}d" if within is not None else ""
    print(f"\nOption expirations (as of {as_of}){winphrase}: {s['n']} positions; "
          f"nearest {s['nearest_expiry']} ({s['nearest_days']}d).")
    print(f"  Premium at risk (long): ${s['total_premium_at_risk']:,.2f}; within {win}d: "
          f"${s['soon_premium_at_risk']:,.2f} across {s['n_expiring_soon']} position(s); ITM now: {s['n_itm']}.")
    if s["total_assignment_cash"] > 0:
        print(f"  Short-put assignment cash if assigned: ${s['total_assignment_cash']:,.2f}.")
    if s["expired"]:
        print(f"  NOTE: {s['expired']} already-expired position(s) present in the data.")
    print("  ITM/OTM uses a spot derived from a held stock lot when available (else n/a; approximate). "
          "[informational, not investment advice]")


def main(argv=None):
    p = argparse.ArgumentParser(prog="portfolio", description="Analyze Fidelity lot exports (read-only).")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite DB path (default: {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("load", help="load a lots CSV into the DB")
    lp.add_argument("csv")
    lp.add_argument("--as-of", help="YYYY-MM-DD term-as-of date (default today)")
    smp = sub.add_parser("summary", help="print standard aggregations")
    smp.add_argument("--as-of", help="YYYY-MM-DD; recompute holding term as of this date (default today)")
    sp = sub.add_parser("symbol", help="detail for one symbol")
    sp.add_argument("sym")
    sp.add_argument("--as-of", help="YYYY-MM-DD; recompute holding term as of this date (default today)")
    sub.add_parser("accounts", help="list accounts")
    qp = sub.add_parser("query", help="run a read-only SELECT over the lots table")
    qp.add_argument("sql")
    hp = sub.add_parser("harvest", help="tax-loss harvest candidates (taxable accounts, short-term first)")
    hp.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    hp.add_argument("--st-rate", type=float, default=0.32, help="short-term/ordinary rate for the estimate")
    hp.add_argument("--lt-rate", type=float, default=0.15, help="long-term rate for the estimate")
    hp.add_argument("--offsetting-st-gains", type=float, default=0.0,
                    help="realized short-term gains these losses can offset (default 0)")
    hp.add_argument("--offsetting-lt-gains", type=float, default=0.0,
                    help="realized long-term gains these losses can offset (default 0)")
    hp.add_argument("--max-ordinary-offset", type=float, default=3000.0,
                    help="max net loss deductible against ordinary income per year (default 3000; MFS 1500)")
    rp = sub.add_parser("ripening", help="taxable short-term lots approaching long-term status")
    rp.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    rp.add_argument("--within", type=int, help="only lots ripening within N days")
    rp.add_argument("--st-rate", type=float, default=0.32, help="short-term/ordinary rate for the estimate")
    rp.add_argument("--lt-rate", type=float, default=0.15, help="long-term rate for the estimate")
    cp = sub.add_parser("concentration", help="cross-account concentration & diversification")
    cp.add_argument("--top", type=int, default=10, help="show the top N positions (default 10)")
    cp.add_argument("--threshold", type=float, default=0.05, help="single-name concentration flag (default 0.05)")
    slp = sub.add_parser("sell", help="pick which lots to sell (specific-ID/HIFO) to minimize tax")
    slp.add_argument("symbol")
    slp.add_argument("shares", type=float)
    slp.add_argument("--account", help="restrict to accounts matching this text")
    slp.add_argument("--strategy", choices=["hifo", "fifo", "loss-first", "min-tax"], default="min-tax")
    slp.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    slp.add_argument("--st-rate", type=float, default=0.32, help="short-term/ordinary rate for the estimate")
    slp.add_argument("--lt-rate", type=float, default=0.15, help="long-term rate for the estimate")
    slp.add_argument("--max-ordinary-offset", type=float, default=3000.0,
                     help="max net loss deductible against ordinary income per year (default 3000; MFS 1500)")
    wp = sub.add_parser("washsale", help="cross-account wash-sale guardrail (needs a Fidelity history CSV)")
    wp.add_argument("history", help="path to an Accounts_History.csv")
    wp.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    wp.add_argument("--window", type=int, default=30, help="wash-sale window in days (default 30)")
    wp.add_argument("--same-underlying", action="store_true",
                    help="also match a stock loss against options on the same underlying (and vice versa)")
    kp = sub.add_parser("capacity", help="bracket-aware realized-gain capacity planner (0%% LTCG / target gain)")
    kp.add_argument("--income", type=float, help="your taxable income basis for the ceiling headroom")
    kp.add_argument("--ceiling", type=float, help="income ceiling to stay under (e.g. top of the 0%% LTCG bracket)")
    kp.add_argument("--ceiling-label", default="0% LTCG", help="name of the ceiling (default '0%% LTCG')")
    kp.add_argument("--target-gain", type=float, help="realize approximately this much long-term gain instead")
    kp.add_argument("--account", help="restrict to accounts matching this text")
    kp.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    kp.add_argument("--lt-rate", type=float, default=0.15, help="long-term rate for the target-gain estimate")
    kp.add_argument("--within-rate", type=float, default=0.0,
                    help="marginal LTCG rate on gains realized below the ceiling (0.0 = the 0%% LTCG bracket)")
    gfp = sub.add_parser("gift", help="appreciated-lot donor picker (taxable long-term gains)")
    gfp.add_argument("--min-gain-pct", type=float, default=0.0,
                     help="only lots with gain%% >= this (percent number, e.g. 20)")
    gfp.add_argument("--top", type=int, default=20, help="show the top N candidates (default 20)")
    gfp.add_argument("--account", help="restrict to accounts matching this text")
    gfp.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    gfp.add_argument("--lt-rate", type=float, default=0.15, help="long-term rate for the tax-avoided estimate")
    dp = sub.add_parser("dashboard", help="year-end tax snapshot (unrealized, harvest, ripening, liquidation, 0%% LTCG)")
    dp.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    dp.add_argument("--st-rate", type=float, default=0.32, help="short-term/ordinary rate for the estimates")
    dp.add_argument("--lt-rate", type=float, default=0.15, help="long-term rate for the estimates")
    dp.add_argument("--within", type=int, default=60, help="ripening horizon in days (default 60)")
    dp.add_argument("--income", type=float, help="taxable income for the 0%% LTCG capacity section")
    dp.add_argument("--ceiling", type=float, help="0%% LTCG bracket top for the capacity section")
    dp.add_argument("--max-ordinary-offset", type=float, default=3000.0,
                    help="max net loss deductible against ordinary income per year (default 3000; MFS 1500)")
    op = sub.add_parser("options", help="options exposure dashboard (premium, notional, moneyness)")
    op.add_argument("--account", help="restrict to accounts matching this text")
    op.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    op.add_argument("--top", type=int, default=20, help="rows to show in each table (default 20)")
    ep = sub.add_parser("expiration", help="option expiration & assignment calendar")
    ep.add_argument("--within", type=int, help="only options expiring within N days")
    ep.add_argument("--account", help="restrict to accounts matching this text")
    ep.add_argument("--as-of", help="YYYY-MM-DD (default today)")
    ep.add_argument("--top", type=int, default=30, help="rows to show (default 30)")
    args = p.parse_args(argv)

    if args.cmd == "load":
        as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
        print(f"Loaded {load(args.csv, args.db, as_of)} lots into {args.db}")
    elif args.cmd == "summary":
        summary(args.db, _as_of(args.as_of))
    elif args.cmd == "symbol":
        symbol_detail(args.db, args.sym, _as_of(args.as_of))
    elif args.cmd == "accounts":
        accounts_list(args.db)
    elif args.cmd == "query":
        rows = run_query(args.db, args.sql)
        if rows is None:
            return 1   # missing/unloaded DB: run_query already printed the hint
        if rows:
            _print_table(list(rows[0].keys()), [tuple(r) for r in rows])
        print(f"({len(rows)} rows)")
    elif args.cmd == "harvest":
        cmd_harvest(args.db, _as_of(args.as_of), args.st_rate, args.lt_rate,
                    args.offsetting_st_gains, args.offsetting_lt_gains, args.max_ordinary_offset)
    elif args.cmd == "ripening":
        cmd_ripening(args.db, _as_of(args.as_of), args.within, args.st_rate, args.lt_rate)
    elif args.cmd == "concentration":
        cmd_concentration(args.db, args.top, args.threshold)
    elif args.cmd == "sell":
        cmd_sell(args.db, args.symbol, args.shares, args.account, args.strategy,
                 _as_of(args.as_of), args.st_rate, args.lt_rate, args.max_ordinary_offset)
    elif args.cmd == "washsale":
        cmd_washsale(args.db, args.history, _as_of(args.as_of), args.window, args.same_underlying)
    elif args.cmd == "capacity":
        cmd_capacity(args.db, args.income, args.ceiling, args.ceiling_label, args.target_gain,
                     args.account, _as_of(args.as_of), args.lt_rate, args.within_rate)
    elif args.cmd == "gift":
        cmd_gift(args.db, args.min_gain_pct, args.top, args.account, _as_of(args.as_of), args.lt_rate)
    elif args.cmd == "dashboard":
        cmd_dashboard(args.db, _as_of(args.as_of), args.st_rate, args.lt_rate, args.within,
                      args.income, args.ceiling, args.max_ordinary_offset)
    elif args.cmd == "options":
        cmd_options(args.db, _as_of(args.as_of), args.account, args.top)
    elif args.cmd == "expiration":
        cmd_expiration(args.db, _as_of(args.as_of), args.within, args.account, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
