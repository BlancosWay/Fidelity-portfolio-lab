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
import re

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
