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


# --- Wash-sale severity classification -------------------------------------------------------------
# Unlike is_taxable's binary split, wash-sale certainty varies by the *type* of tax-advantaged account.
# Employer plans are matched BEFORE IRA/Roth so a "Roth 401(k)" / "BrokerageLink Roth 401(k)" is not
# mis-read as an IRA via the bare word "roth".
_WASH_EMPLOYER = re.compile(r"brokeragelink|brokerage\s+link|401\s*\(?k\)?|403\s*\(?b\)?", re.IGNORECASE)
_WASH_IRA = re.compile(r"\bira\b|\broth\b", re.IGNORECASE)
_WASH_HSA = re.compile(r"\bhsa\b|health\s+savings", re.IGNORECASE)
_WASH_529 = re.compile(r"529", re.IGNORECASE)

# Severity of a replacement buy landing in each account category (informational, not tax advice):
#   ira/hsa  -> BLOCKED : IRA/Roth loss is permanently disallowed (Rev. Rul. 2008-5); HSA is treated
#                         conservatively the same (individual account, no explicit ruling).
#   employer -> REVIEW  : 401(k)/403(b)/BrokerageLink have no IRS wash-sale guidance; prevailing view
#   529      -> REVIEW    is the rule does NOT apply -> softer REVIEW, not BLOCKED.
#   taxable  -> CAUTION : an ordinary wash sale (loss deferred into the replacement lot).
WASH_SEVERITY = {"ira": "BLOCKED", "hsa": "BLOCKED", "employer": "REVIEW", "529": "REVIEW",
                 "taxable": "CAUTION"}

# Precedence for combining multiple triggers: a candidate's status is the worst severity among them.
_STATUS_RANK = {"CLEAN": 0, "REVIEW": 1, "CAUTION": 2, "BLOCKED": 3}


def wash_category(account):
    """Classify an account for WASH-SALE severity: 'employer' | 'ira' | 'hsa' | '529' | 'taxable'.

    Employer plans (401(k)/403(b)/BrokerageLink) are matched first so their common Roth/Traditional
    prefixes don't fall through to the IRA bucket. An empty/unknown name is 'taxable' (the same
    conservative default as ``is_taxable``)."""
    a = account or ""
    if _WASH_EMPLOYER.search(a):
        return "employer"
    if _WASH_IRA.search(a):
        return "ira"
    if _WASH_HSA.search(a):
        return "hsa"
    if _WASH_529.search(a):
        return "529"
    return "taxable"


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


def _per_share_cost(lot):
    """Cost basis per quantity-unit, kept CONSISTENT with ``safe_per_share`` (current_value/quantity).

    Prefers ``Cost Basis Total / quantity`` so OPTION lots compute correctly: Fidelity's per-share
    ``Average Cost Basis`` is the premium per underlying share (not per contract), which would mismatch
    the per-contract proceeds from current_value/quantity. Falls back to ``avg_cost_basis`` only when
    the total is unavailable."""
    try:
        q, c = float(lot.get("quantity")), float(lot.get("cost_basis_total"))
        if q > 0:
            return c / q
    except (TypeError, ValueError):
        pass
    try:
        a = float(lot.get("avg_cost_basis"))
        if a > 0:
            return a
    except (TypeError, ValueError):
        pass
    return None


def _prep_sale_lots(lots, symbol, account, as_of):
    sym = (symbol or "").strip().upper()
    out = []
    for lot in lots:
        if (lot.get("symbol") or "").strip().upper() != sym:
            continue
        if account and account.lower() not in (lot.get("account") or "").lower():
            continue
        price = safe_per_share(lot)
        cost = _per_share_cost(lot)
        if price is None or cost is None:
            continue
        out.append({
            "account": lot.get("account"),
            "symbol": lot.get("symbol"),
            "acquired": (lot.get("date_acquired") or ""),
            "term": recompute_term(lot, as_of),
            "quantity": float(lot.get("quantity")),
            "price": price,
            "cost": cost,
            "per_share_gain": price - cost,
        })
    return out


def _order_sale_lots(prepped, strategy, st_rate, lt_rate):
    if strategy == "hifo":
        return sorted(prepped, key=lambda l: -l["cost"])
    if strategy == "fifo":
        return sorted(prepped, key=lambda l: (l["acquired"] or "9999-99-99"))
    if strategy == "loss-first":
        return sorted(prepped, key=lambda l: l["per_share_gain"])

    def impact(l):  # min-tax: ascending per-share tax impact (losses first, small ST gain < large LT gain)
        rate = st_rate if l["term"] == "Short-Term" else lt_rate
        return l["per_share_gain"] * rate
    return sorted(prepped, key=impact)


def _consume_lots(ordered, shares):
    remaining, picks = shares, []
    for l in ordered:
        if remaining <= 1e-9:
            break
        used = min(remaining, l["quantity"])
        if used <= 0:
            continue
        picks.append({**l, "qty_used": used, "basis": used * l["cost"],
                      "proceeds": used * l["price"], "realized_gain": used * l["per_share_gain"]})
        remaining -= used
    return picks, max(remaining, 0.0)


def select_lots(lots, symbol, shares, strategy="min-tax", account=None, as_of=None,
                st_rate=0.32, lt_rate=0.15):
    """Choose which specific lots to sell to fulfill ``shares`` of ``symbol`` under a strategy:
    hifo (highest cost first), fifo (oldest first), loss-first, or min-tax (ascending per-share tax
    impact, default). Returns ``(picks, summary)`` with realized gain split ST/LT and the delta vs
    FIFO. Proceeds are estimated from current value (a per-share price estimate, not tax advice)."""
    as_of = as_of or dt.date.today()
    prepped = _prep_sale_lots(lots, symbol, account, as_of)
    picks, remaining = _consume_lots(_order_sale_lots(prepped, strategy, st_rate, lt_rate), shares)
    fifo_picks, _ = _consume_lots(_order_sale_lots(prepped, "fifo", st_rate, lt_rate), shares)
    total = sum(p["realized_gain"] for p in picks)
    st_gain = sum(p["realized_gain"] for p in picks if p["term"] == "Short-Term")
    lt_gain = sum(p["realized_gain"] for p in picks if p["term"] == "Long-Term")
    fifo_total = sum(p["realized_gain"] for p in fifo_picks)
    summary = {
        "strategy": strategy,
        "symbol": (symbol or "").strip().upper(),
        "requested_shares": shares,
        "filled_shares": shares - remaining,
        "insufficient": remaining > 1e-9,
        "available_shares": sum(l["quantity"] for l in prepped),
        "realized_gain": total,
        "st_gain": st_gain,
        "lt_gain": lt_gain,
        "fifo_realized_gain": fifo_total,
        "delta_vs_fifo": total - fifo_total,
        "est_tax": st_gain * st_rate + lt_gain * lt_rate,
    }
    return picks, summary


def _securities_match(a, b, same_underlying):
    """Whether two security_key dicts refer to the same security (exact key), or -- when
    same_underlying -- the same underlying (so a stock loss relates to options on it, and vice versa)."""
    if a["key"] and a["key"] == b["key"]:
        return True
    if same_underlying and a["underlying"] and a["underlying"] == b["underlying"]:
        return True
    return False


def _is_acquisition(rec):
    """A purchase that can trigger a wash sale: an equity BUY/REINVEST, or a buy-to-open of a long
    option ("YOU BOUGHT OPENING" -> action_kind OPTION_OPEN). Writing an option (sell-to-open) is not
    an acquisition, so it is excluded."""
    if rec["action_kind"] in ("BUY", "REINVEST"):
        return True
    return rec["action_kind"] == "OPTION_OPEN" and (rec.get("action") or "").upper().startswith("YOU BOUGHT")


def washsale(loss_candidates, history, as_of, window=30, same_underlying=False):
    """Cross-account wash-sale guardrail.

    (a) HARVEST-NOW guard: for each taxable loss candidate, look for an observed BUY/REINVEST of the
        same security in [as_of - window, as_of] in ANY account. A match's severity depends on the
        buying account's ``wash_category``: IRA/Roth/HSA -> BLOCKED (loss disallowed), 401(k)/403(b)/
        BrokerageLink/529 -> REVIEW (unsettled, prevailing view is the rule does not apply), another
        taxable account -> CAUTION. The candidate's status is the worst severity among its triggers.
        (The forward [as_of+1, as_of+window] window is a behavioral warning surfaced by the CLI.)
    (b) REALIZED-history audit: a past SELL is flagged for REVIEW when a same-security BUY/REINVEST
        exists within +/-window days. Whether the sale was at a loss is NOT derivable from history
        alone, so it is labeled "loss unknown" rather than asserted as a wash sale.

    ``history`` records are the dicts returned by ``history.load_history``. Identity uses security_key.
    Returns ``{"candidates": [...], "realized": [...], "summary": {...}}``."""
    buys = [h for h in history if _is_acquisition(h)]
    sells = [h for h in history if h["action_kind"] == "SELL"]

    def sk_of(rec):
        return {"key": rec["sec_key"], "underlying": rec["underlying"]}

    lo, hi = as_of - dt.timedelta(days=window), as_of
    cand_rows = []
    for lot in loss_candidates:
        sk = security_key(lot.get("symbol"))
        triggers = []
        for b in buys:
            if lo <= b["date"] <= hi and _securities_match(sk, sk_of(b), same_underlying):
                category = wash_category(b["account"])
                triggers.append(
                    {"date": b["date"].isoformat(), "account": b["account"], "action": b["action_kind"],
                     "qty": b["abs_qty"], "category": category, "severity": WASH_SEVERITY[category]})
        if not triggers:
            status = "CLEAN"
        else:
            status = max((t["severity"] for t in triggers), key=lambda s: _STATUS_RANK[s])
        cand_rows.append({"symbol": lot.get("symbol"), "account": lot.get("account"),
                          "loss": lot.get("gain_loss"), "status": status, "triggers": triggers})

    realized = []
    for sale in sells:
        sk = security_key(sale.get("symbol"))
        s_lo, s_hi = sale["date"] - dt.timedelta(days=window), sale["date"] + dt.timedelta(days=window)
        matches = [
            {"date": b["date"].isoformat(), "account": b["account"], "action": b["action_kind"],
             "qty": b["abs_qty"]}
            for b in buys
            if s_lo <= b["date"] <= s_hi and _securities_match(sk, sk_of(b), same_underlying)
        ]
        if matches:
            realized.append({"symbol": sale.get("symbol"), "account": sale.get("account"),
                             "date": sale["date"].isoformat(), "status": "REVIEW (loss unknown)",
                             "matches": matches})

    summary = {
        "window": window,
        "blocked": sum(1 for c in cand_rows if c["status"] == "BLOCKED"),
        "caution": sum(1 for c in cand_rows if c["status"] == "CAUTION"),
        "review": sum(1 for c in cand_rows if c["status"] == "REVIEW"),
        "clean": sum(1 for c in cand_rows if c["status"] == "CLEAN"),
        "realized_review": len(realized),
        "history_start": min((h["date"] for h in history), default=None),
        "history_end": max((h["date"] for h in history), default=None),
    }
    return {"candidates": cand_rows, "realized": realized, "summary": summary}
