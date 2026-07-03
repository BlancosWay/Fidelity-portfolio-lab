#!/usr/bin/env python3
"""Pure tax/portfolio analysis logic for Fidelity-portfolio-lab.

Every function here takes plain lot dicts (as returned by ``portfolio.fetch_lots``) and/or history
records (from ``history.load_history``) and returns structured results — no DB access, no printing,
no I/O — so the logic is trivially unit-testable. The ``portfolio.py`` subcommands do the DB read and
the table printing.

A "lot" dict uses the ``lots`` table columns: account, symbol, description, margin_cash, quantity,
date_acquired (ISO string or None), term, avg_cost_basis, cost_basis_total, current_value, gain_loss,
gain_loss_pct.
"""
import datetime as dt
import re

from common import holding_term, one_year_anniversary

# Account is tax-advantaged (no capital-gains treatment -> excluded from harvesting/ripening) when its
# name mentions any of these; otherwise it is a taxable account.
_TAX_ADVANTAGED = re.compile(
    r"\b(roth|ira|hsa|health\s+savings|brokeragelink|brokerage\s+link|401\(?k\)?|403\(?b\)?|529)\b",
    re.IGNORECASE,
)


def is_taxable(account):
    """True for a taxable brokerage account; False for tax-advantaged (IRA/Roth/HSA/401k/529).

    An empty/unknown account name is treated as taxable (the conservative default for surfacing a
    potential harvest — the user reviews before acting)."""
    if not account:
        return True
    return _TAX_ADVANTAGED.search(account) is None


def is_cash(lot):
    """True for the value-only cash/core rows (Symbol=CASH) the exporter emits."""
    return (lot.get("symbol") or "").strip().upper() == "CASH"


def security_key(symbol):
    """Normalize a symbol into {kind: 'stock'|'option', key, underlying}.

    Handles plain tickers ('AAPL', 'BRK.B'), positions-style options ('AAL 20 Call', 'GOOG 200 Put'),
    and history/OCC-style options ('-SOFI270115C30'). ``underlying`` is the base ticker so a stock loss
    can be related to options on the same underlying (and vice versa)."""
    s = (symbol or "").strip()
    up = s.upper()
    if not up:
        return {"kind": "stock", "key": "", "underlying": ""}
    # history / OCC-ish: optional leading '-', TICKER + 6-digit date + C|P + strike
    m = re.match(r"^-?([A-Z]{1,6})\d{6}[CP]\d+(?:\.\d+)?$", up)
    if m:
        return {"kind": "option", "key": up.lstrip("-"), "underlying": m.group(1)}
    # positions-style option: "TICKER STRIKE CALL|PUT"
    m = re.match(r"^([A-Z][A-Z.]*)\s+\d+(?:\.\d+)?\s+(?:CALL|PUT)$", up)
    if m:
        return {"kind": "option", "key": up, "underlying": m.group(1)}
    return {"kind": "stock", "key": up, "underlying": up}


def safe_per_share(lot):
    """Per-share market price = current_value / quantity, or None when quantity<=0 or value missing.

    Never divides by zero (CASH rows and zero/closed lots return None so callers can skip them)."""
    q = lot.get("quantity")
    v = lot.get("current_value")
    try:
        q = float(q)
        v = float(v)
    except (TypeError, ValueError):
        return None
    if q <= 0:
        return None
    return v / q


def taxable_loss_candidates(lots):
    """Lots eligible for tax-loss harvesting: taxable account, unrealized loss (gain_loss < 0),
    excluding cash rows. Returned in input order (callers sort)."""
    out = []
    for lot in lots:
        if is_cash(lot) or not is_taxable(lot.get("account")):
            continue
        gl = lot.get("gain_loss")
        try:
            if gl is not None and float(gl) < 0:
                out.append(lot)
        except (TypeError, ValueError):
            continue
    return out


def lot_acquired_date(lot):
    """datetime.date for a lot's stored ISO ``date_acquired``, or None."""
    d = lot.get("date_acquired")
    if not d:
        return None
    try:
        return dt.date.fromisoformat(str(d))
    except (TypeError, ValueError):
        return None


def recompute_term(lot, as_of):
    """Holding term recomputed from the acquisition date as of ``as_of`` (the DB does not persist
    ``load``'s as-of); falls back to the stored ``term`` when the date is missing."""
    return holding_term(lot_acquired_date(lot), as_of) or (lot.get("term") or "")


def harvest(lots, as_of, st_rate=0.32, lt_rate=0.15):
    """Rank taxable loss lots for harvesting: short-term first (ST losses offset ordinary income),
    then by loss magnitude (most negative first).

    Returns ``(rows, summary)``. ``summary`` keeps signed ST/LT loss totals and a POSITIVE estimated
    avoided-tax benefit ``abs(st_loss)*st_rate + abs(lt_loss)*lt_rate`` (an estimate, not tax advice)."""
    rows = []
    for lot in taxable_loss_candidates(lots):
        rows.append({
            "account": lot.get("account"),
            "symbol": lot.get("symbol"),
            "term": recompute_term(lot, as_of),
            "quantity": lot.get("quantity"),
            "cost_basis_total": lot.get("cost_basis_total"),
            "current_value": lot.get("current_value"),
            "loss": float(lot["gain_loss"]),          # negative
            "loss_pct": lot.get("gain_loss_pct"),
            "is_option": security_key(lot.get("symbol"))["kind"] == "option",
        })
    rows.sort(key=lambda r: (0 if r["term"] == "Short-Term" else 1, r["loss"]))
    st_loss = sum(r["loss"] for r in rows if r["term"] == "Short-Term")
    lt_loss = sum(r["loss"] for r in rows if r["term"] == "Long-Term")
    summary = {
        "st_loss": st_loss,
        "lt_loss": lt_loss,
        "st_lots": sum(1 for r in rows if r["term"] == "Short-Term"),
        "lt_lots": sum(1 for r in rows if r["term"] == "Long-Term"),
        "total_loss": st_loss + lt_loss,
        "est_benefit": abs(st_loss) * st_rate + abs(lt_loss) * lt_rate,
        "has_options": any(r["is_option"] for r in rows),
    }
    return rows, summary


def ripening(lots, as_of, st_rate=0.32, lt_rate=0.15, within=None):
    """Taxable short-term lots and the date each becomes long-term.

    ``ripens_on`` is the first long-term day = one-year anniversary + 1 day (reusing the Feb-29 clamp).
    Winners (gain > 0) show the estimated tax saved by waiting ``gain*(st_rate-lt_rate)``; losers
    (gain < 0) get a "HARVEST BEFORE RIPENING" hint (keep the more valuable short-term loss). Optional
    ``within`` filters to lots ripening within N days. Sorted by ``ripens_on`` ascending."""
    rows = []
    for lot in lots:
        if is_cash(lot) or not is_taxable(lot.get("account")):
            continue
        acq = lot_acquired_date(lot)
        if acq is None or holding_term(acq, as_of) != "Short-Term":
            continue  # only short-term lots ripen
        ripens_on = one_year_anniversary(acq) + dt.timedelta(days=1)
        days_until = (ripens_on - as_of).days
        if within is not None and days_until > within:
            continue
        gl = lot.get("gain_loss")
        gl = float(gl) if gl is not None else 0.0
        if gl > 0:
            hint, tax_saved = "wait for LT", gl * (st_rate - lt_rate)
        elif gl < 0:
            hint, tax_saved = "HARVEST BEFORE RIPENING", 0.0
        else:
            hint, tax_saved = "", 0.0
        rows.append({
            "account": lot.get("account"),
            "symbol": lot.get("symbol"),
            "acquired": acq.isoformat(),
            "ripens_on": ripens_on.isoformat(),
            "days_until": days_until,
            "gain_loss": gl,
            "hint": hint,
            "tax_saved_by_waiting": tax_saved,
        })
    rows.sort(key=lambda r: (r["ripens_on"], r["account"], r["symbol"]))
    summary = {
        "count": len(rows),
        "winners": sum(1 for r in rows if r["gain_loss"] > 0),
        "losers": sum(1 for r in rows if r["gain_loss"] < 0),
        "total_tax_saved_by_waiting": sum(r["tax_saved_by_waiting"] for r in rows),
    }
    return rows, summary


def concentration(lots, top=10, threshold=0.05):
    """Aggregate current market value by symbol across ALL accounts.

    Cash rows are excluded from the single-name statistics but reported separately. Weights are the
    fraction of INVESTED (non-cash) value; HHI = sum(weight^2), effective #positions = 1/HHI. Guards
    the all-cash / zero-invested case (empty rankings, HHI 0, effective positions None, cash 100%)."""
    by_symbol = {}
    cash_total = 0.0
    for lot in lots:
        try:
            cv = float(lot.get("current_value"))
        except (TypeError, ValueError):
            continue
        if is_cash(lot):
            cash_total += cv
            continue
        sym = (lot.get("symbol") or "").strip()
        s = by_symbol.setdefault(sym, {"symbol": sym, "value": 0.0, "accounts": set(),
                                       "is_option": security_key(sym)["kind"] == "option"})
        s["value"] += cv
        s["accounts"].add(lot.get("account"))
    invested = sum(s["value"] for s in by_symbol.values())
    total = invested + cash_total
    if invested <= 0:  # all-cash / zero-invested guard: empty rankings, HHI 0, effective N/A
        return [], {
            "invested_total": invested,
            "cash_total": cash_total,
            "total": total,
            "cash_pct": (cash_total / total) if total > 0 else (1.0 if cash_total > 0 else 0.0),
            "num_positions": 0,
            "hhi": 0.0,
            "effective_positions": None,
            "over_threshold": [],
            "threshold": threshold,
        }
    rows = []
    for s in by_symbol.values():
        w = s["value"] / invested
        rows.append({
            "symbol": s["symbol"],
            "value": s["value"],
            "weight": w,
            "accounts": len(s["accounts"]),
            "is_option": s["is_option"],
            "over_threshold": w > threshold,
        })
    rows.sort(key=lambda r: -r["value"])
    cum = 0.0
    for r in rows:
        cum += r["weight"]
        r["cumulative"] = cum
    hhi = sum(r["weight"] ** 2 for r in rows)
    summary = {
        "invested_total": invested,
        "cash_total": cash_total,
        "total": total,
        "cash_pct": (cash_total / total) if total > 0 else 0.0,
        "num_positions": len(rows),
        "hhi": hhi,
        "effective_positions": (1.0 / hhi) if hhi > 0 else None,
        "over_threshold": [r["symbol"] for r in rows if r["over_threshold"]],
        "threshold": threshold,
    }
    return rows, summary
