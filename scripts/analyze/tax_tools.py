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

from common import holding_term, one_year_anniversary, parse_date

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
_WASH_529 = re.compile(r"\b529\b", re.IGNORECASE)

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


def price_dispersion_flags(lots, tol=0.02):
    """Symbols whose per-share price (``safe_per_share`` = current_value/quantity) is INCONSISTENT
    across their lots -- a known Fidelity browser-scrape corruption. All lots of a symbol share one
    market price, so any real spread is a data-quality problem the caller should warn about (this
    detector never alters numbers).

    Returns ``{symbol: {"min": .., "max": .., "spread": ..}}`` for each non-cash, non-option symbol
    with >=2 priced lots whose relative spread ``(max-min)/max`` exceeds ``tol`` (computed only when
    ``max > 0``)."""
    prices = {}
    for lot in lots:
        if is_cash(lot) or security_key(lot.get("symbol"))["kind"] == "option":
            continue
        sp = safe_per_share(lot)
        if sp is None:
            continue
        prices.setdefault((lot.get("symbol") or "").strip().upper(), []).append(sp)
    flags = {}
    for sym, ps in prices.items():
        if len(ps) < 2:
            continue
        lo, hi = min(ps), max(ps)
        if hi > 0 and (hi - lo) / hi > tol:
            flags[sym] = {"min": lo, "max": hi, "spread": (hi - lo) / hi}
    return flags


def _live_quantity(lot):
    """A lot is a LIVE (open) position only when its quantity parses to a strictly positive number.
    Zero/negative/blank/non-numeric quantities are closed or unparseable lots, so the live-position
    analyses (harvest candidates, liquidation, unrealized-by-account) skip them."""
    try:
        return float(lot.get("quantity")) > 0
    except (TypeError, ValueError):
        return False


def taxable_loss_candidates(lots):
    """Lots eligible for tax-loss harvesting: taxable account, LIVE (quantity > 0) position, unrealized
    loss (gain_loss < 0), excluding cash rows. Returned in input order (callers sort)."""
    out = []
    for lot in lots:
        if is_cash(lot) or not is_taxable(lot.get("account")) or not _live_quantity(lot):
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


def harvest(lots, as_of, st_rate=0.32, lt_rate=0.15, offsetting_st_gains=0.0, offsetting_lt_gains=0.0,
            max_ordinary_offset=3000.0):
    """Rank taxable loss lots for harvesting: short-term first (ST losses offset ordinary income),
    then by loss magnitude (most negative first).

    Returns ``(rows, summary)``. ``est_benefit`` is the current-year tax reduction from realizing these
    losses, computed as tax-without minus tax-with via ``_net_capital_tax`` against any known
    ``offsetting_st_gains``/``offsetting_lt_gains`` (default 0 = no offsetting gains, so the benefit is
    the ``max_ordinary_offset``-capped ordinary-income offset). ``carryforward_loss`` is the excess that
    carries to future years. An estimate, not tax advice."""
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
    st_loss = sum(r["loss"] for r in rows if r["term"] == "Short-Term")   # <= 0
    lt_loss = sum(r["loss"] for r in rows if r["term"] == "Long-Term")    # <= 0
    tax_without = _net_capital_tax(offsetting_st_gains, offsetting_lt_gains, st_rate, lt_rate,
                                   max_ordinary_offset)
    tax_with = _net_capital_tax(offsetting_st_gains + st_loss, offsetting_lt_gains + lt_loss,
                                st_rate, lt_rate, max_ordinary_offset)
    summary = {
        "st_loss": st_loss,
        "lt_loss": lt_loss,
        "st_lots": sum(1 for r in rows if r["term"] == "Short-Term"),
        "lt_lots": sum(1 for r in rows if r["term"] == "Long-Term"),
        "total_loss": st_loss + lt_loss,
        "est_benefit": tax_without["est_tax"] - tax_with["est_tax"],   # >= 0
        "carryforward_loss": tax_with["carryforward"],
        "has_options": any(r["is_option"] for r in rows),
    }
    return rows, summary


def ripening(lots, as_of, st_rate=0.32, lt_rate=0.15, within=None):
    """Taxable, LIVE (quantity > 0) short-term lots and the date each becomes long-term.

    ``ripens_on`` is the first long-term day = one-year anniversary + 1 day (reusing the Feb-29 clamp).
    Winners (gain > 0) show the estimated tax saved by waiting ``gain*(st_rate-lt_rate)``; losers
    (gain < 0) get a "HARVEST BEFORE RIPENING" hint (keep the more valuable short-term loss). Optional
    ``within`` filters to lots ripening within N days. Sorted by ``ripens_on`` ascending."""
    rows = []
    for lot in lots:
        if is_cash(lot) or not is_taxable(lot.get("account")) or not _live_quantity(lot):
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
    """Aggregate current market value by symbol across ALL accounts (single-name equity concentration).

    Cash rows are excluded from the single-name statistics but reported separately. **Options are
    excluded** from the equity ranking -- a lot's ``current_value`` is the option *premium*, not the
    notional exposure, so ranking it as an equity position is misleading (use the ``options`` command);
    the number of option lots dropped is reported as ``n_options_excluded``. Symbols whose **aggregated
    value is non-positive** (a short position or a corrupt/negative scrape) are excluded from the ranking
    and from invested so a single bad value cannot collapse the whole report; the count is reported as
    ``n_nonpositive_excluded``. Weights are the fraction of INVESTED (non-cash, non-option, positive)
    value; HHI = sum(weight^2), effective #positions = 1/HHI. Guards the all-cash / zero-invested case
    (empty rankings, HHI 0, effective positions None, cash 100%)."""
    by_symbol = {}
    cash_total = 0.0
    n_options_excluded = 0
    for lot in lots:
        try:
            cv = float(lot.get("current_value"))
        except (TypeError, ValueError):
            continue
        if is_cash(lot):
            cash_total += cv
            continue
        sym = (lot.get("symbol") or "").strip()
        if security_key(sym)["kind"] == "option":
            n_options_excluded += 1        # premium != notional exposure; see the `options` command
            continue
        s = by_symbol.setdefault(sym, {"symbol": sym, "value": 0.0, "accounts": set()})
        s["value"] += cv
        s["accounts"].add(lot.get("account"))
    # Drop symbols whose aggregated value is non-positive (short/corrupt) so `invested` can't collapse.
    positive = {sym: s for sym, s in by_symbol.items() if s["value"] > 0}
    n_nonpositive_excluded = len(by_symbol) - len(positive)
    invested = sum(s["value"] for s in positive.values())
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
            "n_options_excluded": n_options_excluded,
            "n_nonpositive_excluded": n_nonpositive_excluded,
        }
    rows = []
    for s in positive.values():
        w = s["value"] / invested
        rows.append({
            "symbol": s["symbol"],
            "value": s["value"],
            "weight": w,
            "accounts": len(s["accounts"]),
            "is_option": False,   # options are excluded from the ranking; kept for backward compatibility
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
        "n_options_excluded": n_options_excluded,
        "n_nonpositive_excluded": n_nonpositive_excluded,
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
        if not is_taxable(lot.get("account")):
            continue  # tax-advantaged (Roth/IRA/HSA/...) lots are never tax-optimized sale candidates
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
                st_rate=0.32, lt_rate=0.15, max_ordinary_offset=3000.0):
    """Choose which specific lots to sell to fulfill ``shares`` of ``symbol`` under a strategy:
    hifo (highest cost first), fifo (oldest first), loss-first, or min-tax (ascending per-share tax
    impact, default). Only **taxable** accounts are considered (tax-advantaged lots are excluded --
    their gains are tax-free and a specific-ID sale there isn't a tax-optimized taxable sale). Returns
    ``(picks, summary)`` with realized gain split ST/LT, the delta vs FIFO, and ``accounts`` /
    ``multi_account`` (a sale spanning accounts is more than one broker order). ``est_tax`` nets the
    realized ST vs LT via ``_net_capital_tax`` (a net loss benefit is capped at ``max_ordinary_offset``,
    with the residual as ``carryforward``), consistent with harvest/liquidation/dashboard. Proceeds are
    estimated from current value (a per-share price estimate, not tax advice)."""
    as_of = as_of or dt.date.today()
    prepped = _prep_sale_lots(lots, symbol, account, as_of)
    picks, remaining = _consume_lots(_order_sale_lots(prepped, strategy, st_rate, lt_rate), shares)
    fifo_picks, _ = _consume_lots(_order_sale_lots(prepped, "fifo", st_rate, lt_rate), shares)
    total = sum(p["realized_gain"] for p in picks)
    st_gain = sum(p["realized_gain"] for p in picks if p["term"] == "Short-Term")
    lt_gain = sum(p["realized_gain"] for p in picks if p["term"] == "Long-Term")
    fifo_total = sum(p["realized_gain"] for p in fifo_picks)
    accounts = sorted({p["account"] for p in picks}, key=lambda a: a or "")
    nct = _net_capital_tax(st_gain, lt_gain, st_rate, lt_rate, max_ordinary_offset)
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
        "est_tax": nct["est_tax"],
        "net_gain": nct["net_gain"],
        "net_loss": nct["net_loss"],
        "deductible_loss": nct["deductible_loss"],
        "carryforward": nct["carryforward"],
        "accounts": accounts,
        "multi_account": len(accounts) > 1,
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
    """A purchase that can trigger a wash sale: a definite equity BUY/REINVEST, a buy-to-open of a long
    option ("YOU BOUGHT OPENING" -> action_kind OPTION_OPEN), or an INFERRED re-acquisition via option
    assignment/exercise or an inbound transfer/exchange/journal (action_kind ACQUIRE_INFERRED) that
    actually brought shares in (``signed_qty > 0``). Writing an option (sell-to-open) or an outbound
    transfer (signed_qty <= 0) is not an acquisition, so it is excluded."""
    if rec["action_kind"] in ("BUY", "REINVEST"):
        return True
    if rec["action_kind"] == "OPTION_OPEN" and (rec.get("action") or "").upper().startswith("YOU BOUGHT"):
        return True
    if rec["action_kind"] == "ACQUIRE_INFERRED":
        try:
            return float(rec.get("signed_qty")) > 0
        except (TypeError, ValueError):
            return False
    return False


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
    Each candidate also carries ``affected_shares`` (shares matched by replacement purchases) and
    ``disallowed_loss`` (the quantity-apportioned share of the loss that is disallowed; the rest of the
    loss stays allowed). Returns ``{"candidates": [...], "realized": [...], "summary": {...}}``."""
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
                inferred = b["action_kind"] == "ACQUIRE_INFERRED"
                # An inferred (non-BUY) re-acquisition is never asserted as a definite wash sale: cap it
                # at REVIEW regardless of account category. Definite BUY/REINVEST/buy-to-open keep the map.
                severity = "REVIEW" if inferred else WASH_SEVERITY[category]
                triggers.append(
                    {"date": b["date"].isoformat(), "account": b["account"], "action": b["action_kind"],
                     "qty": b["abs_qty"], "category": category, "severity": severity, "inferred": inferred})
        if not triggers:
            status = "CLEAN"
        else:
            status = max((t["severity"] for t in triggers), key=lambda s: _STATUS_RANK[s])
        # Quantity-aware disallowed loss: only the loss on shares matched by replacement purchases is
        # disallowed. affected_shares = min(total matched replacement qty, loss-lot qty); the disallowed
        # amount is that share fraction of the loss (the rest of the loss remains allowed).
        try:
            loss_qty = abs(float(lot.get("quantity")))
        except (TypeError, ValueError):
            loss_qty = 0.0
        try:
            loss_amt = float(lot.get("gain_loss"))
        except (TypeError, ValueError):
            loss_amt = 0.0
        matched_qty = sum(t["qty"] for t in triggers)
        affected_shares = min(matched_qty, loss_qty) if loss_qty > 0 else 0.0
        disallowed_loss = (loss_amt * affected_shares / loss_qty) if loss_qty > 0 else 0.0
        cand_rows.append({"symbol": lot.get("symbol"), "account": lot.get("account"),
                          "loss": lot.get("gain_loss"), "status": status, "triggers": triggers,
                          "affected_shares": affected_shares, "disallowed_loss": disallowed_loss})

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


def gain_capacity(lots, as_of, income=None, ceiling=None, target_gain=None, account=None,
                  lt_rate=0.15, within_rate=0.0):
    """Bracket-aware realized-gain capacity planner (informational, NOT tax advice).

    Selects taxable LONG-TERM GAIN lots to realize either an explicit ``target_gain`` or the headroom
    ``max(0, ceiling - income)`` to a user-supplied income ceiling, consuming lots largest-gain-first
    and taking the final lot partially. Options, short-term lots, losses and tax-advantaged accounts
    are excluded (gain-harvesting applies to taxable long-term gains). ``within_rate`` is the marginal
    long-term capital-gains rate on gains realized BELOW the ceiling: the default ``0.0`` is the top of
    the 0% long-term bracket (gain is tax-free); pass the real LTCG rate for an NIIT/IRMAA ceiling,
    which only avoids the surcharge/tier while the gain itself is still taxed.

    Returns ``(picks, summary)``; ``picks == []`` when no budget is supplied or the budget is <= 0."""
    acct = (account or "").lower()
    candidates = []
    for lot in lots:
        if is_cash(lot) or not is_taxable(lot.get("account")):
            continue
        if security_key(lot.get("symbol"))["kind"] == "option":
            continue
        if recompute_term(lot, as_of) != "Long-Term":
            continue
        try:
            gain = float(lot.get("gain_loss"))
            qty = float(lot.get("quantity"))
        except (TypeError, ValueError):
            continue
        if gain <= 0 or qty <= 0:
            continue
        if acct and acct not in (lot.get("account") or "").lower():
            continue
        try:
            basis = float(lot.get("cost_basis_total"))
        except (TypeError, ValueError):
            basis = None
        try:
            value = float(lot.get("current_value"))
        except (TypeError, ValueError):
            value = None
        gp = lot.get("gain_loss_pct")
        candidates.append({
            "account": lot.get("account"), "symbol": lot.get("symbol"),
            "acquired": lot.get("date_acquired") or "", "quantity": qty,
            "basis": basis, "value": value, "gain": gain,
            "gain_pct": gp if isinstance(gp, (int, float)) else None,
        })
    available_gain = sum(c["gain"] for c in candidates)

    if target_gain is not None:
        budget, source = float(target_gain), "target-gain"
    elif income is not None and ceiling is not None:
        budget, source = max(0.0, float(ceiling) - float(income)), "headroom"
    else:
        budget, source = None, "inventory-only"

    picks, realized = [], 0.0
    if budget is not None and budget > 0:
        ordered = sorted(candidates, key=lambda c: (
            -c["gain"], -(c["gain_pct"] if c["gain_pct"] is not None else -1e18), c["symbol"] or ""))
        cum = 0.0
        for c in ordered:
            if cum >= budget - 1e-9:
                break
            remaining = budget - cum
            if c["gain"] <= remaining + 1e-9:
                frac, partial = 1.0, False
            else:
                frac, partial = remaining / c["gain"], True
            picks.append({
                **c,
                "qty_used": c["quantity"] * frac,
                "gain_used": c["gain"] * frac,
                "basis_used": (c["basis"] * frac) if c["basis"] is not None else None,
                "value_used": (c["value"] * frac) if c["value"] is not None else None,
                "partial": partial,
            })
            cum += c["gain"] * frac
        realized = cum

    has_ceiling = income is not None and ceiling is not None
    if source == "headroom":
        est_tax = realized * within_rate
    elif source == "target-gain":
        est_tax = realized * lt_rate
    else:
        est_tax = None
    summary = {
        "source": source,
        "budget": budget,
        "available_gain": available_gain,
        "realized": realized,
        "remaining_budget": (max(0.0, budget - realized) if budget is not None else None),
        "leftover_gain": max(0.0, available_gain - realized),
        "constrained_by": ("inventory" if (budget is not None and available_gain < budget)
                           else ("budget" if budget is not None else "none")),
        "income": income,
        "ceiling": ceiling,
        "headroom": (max(0.0, float(ceiling) - float(income)) if has_ceiling else None),
        "above_ceiling": has_ceiling and float(income) >= float(ceiling),
        "est_tax": est_tax,
        "n_candidates": len(candidates),
        "lt_rate": lt_rate,
        "within_rate": within_rate,
    }
    return picks, summary


def gift_candidates(lots, as_of, min_gain_pct=0.0, account=None, lt_rate=0.15):
    """Appreciated-lot donor picker (informational, NOT tax advice).

    Donating an appreciated LONG-TERM security avoids the capital-gains tax and (if you itemize)
    deducts fair market value. Surfaces the best taxable, LIVE (quantity > 0) long-term gain lots to
    donate, ranked by gain% (most-appreciated first); short-term-gain and loss lots are counted
    separately and steered elsewhere (wait for long-term / harvest instead). ``min_gain_pct`` is a
    PERCENT number (20 == 20%). A positive ``min_gain_pct`` requires a computable gain%; at the default
    0 a positive gain suffices. Returns ``(rows, summary)``."""
    acct = (account or "").lower()
    rows, n_short_term_gain, n_loss = [], 0, 0
    for lot in lots:
        if is_cash(lot) or not is_taxable(lot.get("account")) or not _live_quantity(lot):
            continue
        if security_key(lot.get("symbol"))["kind"] == "option":
            continue
        if acct and acct not in (lot.get("account") or "").lower():
            continue
        try:
            gain = float(lot.get("gain_loss"))
        except (TypeError, ValueError):
            continue
        term = recompute_term(lot, as_of)
        if gain < 0:
            n_loss += 1
            continue
        if gain > 0 and term == "Short-Term":
            n_short_term_gain += 1
            continue
        if not (gain > 0 and term == "Long-Term"):
            continue  # zero gain or unknown term: neither a candidate nor an anti-bucket
        gp = lot.get("gain_loss_pct")
        if isinstance(gp, (int, float)):
            gain_pct = float(gp)
        else:
            try:
                b = float(lot.get("cost_basis_total"))
                gain_pct = (gain / b * 100.0) if b > 0 else None
            except (TypeError, ValueError):
                gain_pct = None
        if min_gain_pct > 0 and (gain_pct is None or gain_pct < min_gain_pct):
            continue
        try:
            value = float(lot.get("current_value"))
        except (TypeError, ValueError):
            value = None
        try:
            basis = float(lot.get("cost_basis_total"))
        except (TypeError, ValueError):
            basis = None
        rows.append({
            "account": lot.get("account"), "symbol": lot.get("symbol"),
            "acquired": lot.get("date_acquired") or "", "quantity": lot.get("quantity"),
            "basis": basis, "value": value, "gain": gain, "gain_pct": gain_pct,
            "tax_avoided": gain * lt_rate,
        })
    rows.sort(key=lambda r: (-(r["gain_pct"] if r["gain_pct"] is not None else -1e18),
                             -r["gain"], r["symbol"] or ""))
    summary = {
        "n_candidates": len(rows),
        "total_fmv": sum(r["value"] for r in rows if r["value"] is not None),
        "total_gain": sum(r["gain"] for r in rows),
        "total_tax_avoided": sum(r["tax_avoided"] for r in rows),
        "n_short_term_gain": n_short_term_gain,
        "n_loss": n_loss,
        "min_gain_pct": min_gain_pct,
        "lt_rate": lt_rate,
    }
    return rows, summary


def holdings_overview(lots, as_of):
    """Portfolio holdings aggregations with the holding **term recomputed as of ``as_of``** (the DB
    does not persist ``load``'s as-of, so the stored ``term`` can be stale). Pure function over the
    lot dicts; mirrors the three ``summary`` tables exactly:

      * ``by_symbol``   -- every lot grouped by symbol (**cash included**): total units, lot count,
                           distinct-account count, and the long/short unit split (cash, whose
                           recomputed term is blank, contributes 0 to the split).
      * ``term_totals`` -- lot count and market value for Long-Term vs Short-Term **only** (cash is
                           excluded here -- and ONLY here -- because its term is blank).
      * ``by_account``  -- every lot grouped by account (**cash included in market value**): long/short
                           lot counts and total market value.

    All figures are informational, NOT tax advice."""
    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    by_symbol, by_account, term_totals = {}, {}, {}
    for lot in lots:
        sym = (lot.get("symbol") or "").strip()
        acct = (lot.get("account") or "").strip()
        term = recompute_term(lot, as_of)
        qty = _num(lot.get("quantity"))
        val = _num(lot.get("current_value"))

        s = by_symbol.setdefault(sym, {"symbol": sym, "units": 0.0, "lots": 0,
                                       "accounts": set(), "long_units": 0.0, "short_units": 0.0})
        s["units"] += qty
        s["lots"] += 1
        s["accounts"].add(acct)
        if term == "Long-Term":
            s["long_units"] += qty
        elif term == "Short-Term":
            s["short_units"] += qty

        a = by_account.setdefault(acct, {"account": acct, "long_lots": 0, "short_lots": 0,
                                         "market_value": 0.0})
        a["market_value"] += val
        if term == "Long-Term":
            a["long_lots"] += 1
        elif term == "Short-Term":
            a["short_lots"] += 1

        if term in ("Long-Term", "Short-Term"):
            t = term_totals.setdefault(term, {"term": term, "lots": 0, "market_value": 0.0})
            t["lots"] += 1
            t["market_value"] += val

    by_symbol_rows = [{
        "symbol": by_symbol[k]["symbol"], "units": round(by_symbol[k]["units"], 4),
        "lots": by_symbol[k]["lots"], "accts": len(by_symbol[k]["accounts"]),
        "long_units": round(by_symbol[k]["long_units"], 4),
        "short_units": round(by_symbol[k]["short_units"], 4),
    } for k in sorted(by_symbol)]
    term_rows = [{
        "term": term_totals[k]["term"], "lots": term_totals[k]["lots"],
        "market_value": round(term_totals[k]["market_value"], 2),
    } for k in sorted(term_totals)]
    account_rows = [{
        "account": by_account[k]["account"], "long_lots": by_account[k]["long_lots"],
        "short_lots": by_account[k]["short_lots"], "market_value": round(by_account[k]["market_value"], 2),
    } for k in sorted(by_account)]
    return {"by_symbol": by_symbol_rows, "term_totals": term_rows, "by_account": account_rows}


def unrealized_by_account(lots, as_of):
    """Per-account unrealized gain/loss split short-term vs long-term (informational, NOT tax advice).

    Non-cash LIVE lots (quantity > 0) with a numeric ``gain_loss``; term via ``recompute_term`` (not the
    stale stored term). Returns ``(rows, summary)`` where each row has account, taxable, st_gl, lt_gl,
    total_gl, market_value, and the summary carries taxable/tax-advantaged ST/LT subtotals + total_gl."""
    by = {}
    for lot in lots:
        if is_cash(lot) or not _live_quantity(lot):
            continue
        try:
            gl = float(lot.get("gain_loss"))
        except (TypeError, ValueError):
            continue
        try:
            cv = float(lot.get("current_value"))
        except (TypeError, ValueError):
            cv = 0.0
        acct = lot.get("account")
        rec = by.setdefault(acct, {"account": acct, "taxable": is_taxable(acct),
                                   "st_gl": 0.0, "lt_gl": 0.0, "total_gl": 0.0, "market_value": 0.0})
        term = recompute_term(lot, as_of)
        if term == "Short-Term":
            rec["st_gl"] += gl
        elif term == "Long-Term":
            rec["lt_gl"] += gl
        rec["total_gl"] += gl
        rec["market_value"] += cv
    rows = sorted(by.values(), key=lambda r: r["account"] or "")
    summary = {
        "taxable_st": sum(r["st_gl"] for r in rows if r["taxable"]),
        "taxable_lt": sum(r["lt_gl"] for r in rows if r["taxable"]),
        "adv_st": sum(r["st_gl"] for r in rows if not r["taxable"]),
        "adv_lt": sum(r["lt_gl"] for r in rows if not r["taxable"]),
        "total_gl": sum(r["total_gl"] for r in rows),
    }
    return rows, summary


def _net_capital_tax(st, lt, st_rate=0.32, lt_rate=0.15, max_ordinary_offset=3000.0):
    """Single-year capital-gains tax on signed short-term (``st``) and long-term (``lt``) totals
    (an estimate, NOT tax advice). Nets ST and LT together: a loss in one bucket first offsets a gain
    in the other. A residual net GAIN is taxed at the surviving (winning) bucket's rate and is never
    negative. A residual net LOSS offsets ordinary income up to ``max_ordinary_offset``/yr (default
    $3,000; married-filing-separately is $1,500) at ``st_rate`` (a benefit), with the remainder carried
    forward. Ignores state tax, NIIT, and wash-sale interactions.

    Returns ``{est_tax, net_gain, net_loss, deductible_loss, carryforward}``."""
    net = st + lt
    if net >= 0:
        if st >= 0 and lt >= 0:
            est_tax = st * st_rate + lt * lt_rate          # both gains: tax each bucket
        else:
            # exactly one bucket is a loss; it fully nets the other, leaving `net` at the winner's rate
            est_tax = net * (st_rate if st > 0 else lt_rate)
        return {"est_tax": est_tax, "net_gain": net, "net_loss": 0.0,
                "deductible_loss": 0.0, "carryforward": 0.0}
    loss = -net
    deductible = min(max(max_ordinary_offset, 0.0), loss)   # clamp: a negative cap can't create a benefit
    return {"est_tax": -(deductible * st_rate), "net_gain": 0.0, "net_loss": loss,
            "deductible_loss": deductible, "carryforward": loss - deductible}


def liquidation_estimate(lots, as_of, st_rate=0.32, lt_rate=0.15, max_ordinary_offset=3000.0):
    """Estimated tax if every taxable non-cash lot were sold now (informational, NOT tax advice).

    Sums signed short-term and long-term ``gain_loss`` (term via ``recompute_term``) over taxable,
    LIVE (quantity > 0) lots, then nets them via ``_net_capital_tax``: a net gain is taxed (never
    negative), a net loss yields a benefit capped at the ``max_ordinary_offset`` ordinary-income offset
    plus a carryforward."""
    st_gain = lt_gain = 0.0
    n_lots = 0
    for lot in lots:
        if is_cash(lot) or not is_taxable(lot.get("account")) or not _live_quantity(lot):
            continue
        try:
            gl = float(lot.get("gain_loss"))
        except (TypeError, ValueError):
            continue
        term = recompute_term(lot, as_of)
        if term == "Short-Term":
            st_gain += gl
        elif term == "Long-Term":
            lt_gain += gl
        else:
            continue
        n_lots += 1
    net = _net_capital_tax(st_gain, lt_gain, st_rate, lt_rate, max_ordinary_offset)
    return {
        "st_gain": st_gain,
        "lt_gain": lt_gain,
        "total_gain": st_gain + lt_gain,
        "est_tax": net["est_tax"],
        "net_gain": net["net_gain"],
        "net_loss": net["net_loss"],
        "deductible_loss": net["deductible_loss"],
        "carryforward": net["carryforward"],
        "n_lots": n_lots,
    }


# --- Options (Tier-3) ------------------------------------------------------------------------------
_OPT_POS_RE = re.compile(r"^([A-Za-z][A-Za-z.]*)\s+(\d+(?:\.\d+)?)\s+(call|put)$", re.IGNORECASE)
_OPT_OCC_RE = re.compile(r"^-?([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d+(?:\.\d+)?)$")
# A month-day-year token anywhere in an option Description. Fidelity exports either a bare date
# ("Jul-17-2026") or the full contract name ("AAPL JAN 16 2026 $250 CALL"); extract the date from both.
_DATE_TOKEN_RE = re.compile(r"[A-Za-z]{3}[-\s]\d{1,2}[-,\s]+\d{4}")


def _option_expiry(description):
    """Expiry date from an option Description: the whole field when it is a bare date, else the first
    month-day-year token embedded in a full contract name. Returns a date or None."""
    if not description:
        return None
    d = parse_date(description)
    if d is not None:
        return d
    m = _DATE_TOKEN_RE.search(description)
    return parse_date(m.group(0)) if m else None


def parse_option(lot):
    """Parse an option lot -> {underlying, strike, type, expiry, contracts, long, multiplier} or None.

    Uses the SAME normalization as ``security_key`` (strip; positions regex on the stripped symbol,
    OCC regex on its uppercase) so any lot ``security_key`` calls an option parses here rather than
    being silently dropped. Positions style ``'AAL 17 Call'`` takes the expiry from the Description
    column (a bare date OR a full contract name like ``'AAPL JAN 16 2026 $250 CALL'``); the OCC/history
    style ``'-SOFI270115C30'`` packs it in the symbol. ``contracts`` is signed (negative = written/short);
    ``multiplier`` is the standard 100 shares/contract."""
    sym = (lot.get("symbol") or "").strip()
    if security_key(sym)["kind"] != "option":
        return None
    try:
        contracts = float(lot.get("quantity"))
    except (TypeError, ValueError):
        contracts = 0.0
    m = _OPT_POS_RE.match(sym)
    if m:
        underlying, strike, otype = m.group(1).upper(), float(m.group(2)), m.group(3).lower()
        expiry = _option_expiry(lot.get("description"))
    else:
        m = _OPT_OCC_RE.match(sym.upper())
        if not m:
            return None
        underlying = m.group(1)
        try:
            expiry = dt.date(2000 + int(m.group(2)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            expiry = None
        otype = "call" if m.group(5) == "C" else "put"
        strike_tok = m.group(6)
        # Standard OCC packs the strike as 8 digits in thousandths of a dollar (e.g. "00150000" = 150.00);
        # Fidelity's short history style writes the plain strike (e.g. "C30" = 30). Disambiguate on the
        # exact 8-digit, no-decimal OCC encoding so a short-style strike is never mis-scaled.
        if len(strike_tok) == 8 and "." not in strike_tok:
            strike = int(strike_tok) / 1000.0
        else:
            strike = float(strike_tok)
    return {"underlying": underlying, "strike": strike, "type": otype, "expiry": expiry,
            "contracts": contracts, "long": contracts >= 0, "multiplier": 100}


def underlying_spots(lots):
    """Map underlying symbol (upper) -> current per-share price from held non-option, non-cash lots.

    Uses the per-share price of the lot with the LARGEST current value for each symbol: the export's
    per-share values can be inconsistent across a symbol's lots (partial/stale scrapes), so the biggest
    dollar position is the most reliable single estimate. The spot is approximate and is surfaced so the
    user can sanity-check it; it drives only option moneyness (ITM/OTM), never a dollar figure."""
    best = {}  # symbol -> (current_value, per_share)
    for lot in lots:
        if is_cash(lot) or security_key(lot.get("symbol"))["kind"] == "option":
            continue
        sp = safe_per_share(lot)
        if sp is None:
            continue
        try:
            cv = float(lot.get("current_value"))
        except (TypeError, ValueError):
            continue
        u = (lot.get("symbol") or "").strip().upper()
        if u not in best or cv > best[u][0]:
            best[u] = (cv, sp)
    return {u: v[1] for u, v in best.items()}


def _moneyness(otype, strike, spot):
    if spot is None:
        return "n/a"
    if spot == strike:
        return "ATM"
    if otype == "call":
        return "ITM" if spot > strike else "OTM"
    return "ITM" if spot < strike else "OTM"


def options_exposure(lots, as_of, account=None):
    """Options exposure dashboard (informational, NOT investment advice).

    Returns ``(positions, by_underlying, summary)``. Each position carries premium at risk
    (current value), notional (strike*100*abs(contracts)), moneyness (from a spot derived from a held
    stock lot, else "n/a"), and long/short (by quantity sign). ``by_underlying`` aggregates contracts
    by call/put and long/short, directional notionals (bullish = long call + short put; bearish = long
    put + short call),     covered-vs-naked short calls (vs shares held IN THE SAME ACCOUNT) and short-put assignment cash.
    Delta/theta are not computed (need live quotes). ``spots`` (moneyness) are account-independent
    market prices; the ``account`` filter restricts positions AND same-account share coverage."""
    acct = (account or "").lower()
    spots = underlying_spots(lots)  # market price is account-independent -> global spot

    def _match(l):
        return not acct or acct in (l.get("account") or "").lower()

    # Same-account share coverage: shares per (account, underlying); held_u = per-underlying total (view).
    shares_au, held_u = {}, {}
    for lot in lots:
        if is_cash(lot) or security_key(lot.get("symbol"))["kind"] == "option" or not _match(lot):
            continue
        try:
            q = float(lot.get("quantity"))
        except (TypeError, ValueError):
            continue
        u = (lot.get("symbol") or "").strip().upper()
        a = (lot.get("account") or "").lower()
        shares_au[(a, u)] = shares_au.get((a, u), 0.0) + q
        held_u[u] = held_u.get(u, 0.0) + q

    positions = []
    short_calls_au = {}  # (account_lower, underlying) -> short call contracts, for same-account coverage
    n_expired_excluded = 0
    for lot in lots:
        po = parse_option(lot)
        if po is None or not _match(lot):
            continue
        if po["expiry"] is not None and po["expiry"] < as_of:
            n_expired_excluded += 1        # an expired contract is not live exposure
            continue
        spot = spots.get(po["underlying"])
        try:
            premium = float(lot.get("current_value"))
        except (TypeError, ValueError):
            premium = 0.0
        try:
            cost = float(lot.get("cost_basis_total"))
        except (TypeError, ValueError):
            cost = None
        contracts = po["contracts"]
        notional = po["strike"] * po["multiplier"] * abs(contracts)
        days = (po["expiry"] - as_of).days if po["expiry"] else None
        if po["type"] == "call" and not po["long"]:
            key = ((lot.get("account") or "").lower(), po["underlying"])
            short_calls_au[key] = short_calls_au.get(key, 0.0) + abs(contracts)
        positions.append({
            "account": lot.get("account"), "underlying": po["underlying"], "type": po["type"],
            "strike": po["strike"], "expiry": po["expiry"].isoformat() if po["expiry"] else "",
            "days_to_expiry": days, "contracts": contracts, "long": po["long"], "premium": premium,
            "notional": notional, "cost": cost, "moneyness": _moneyness(po["type"], po["strike"], spot),
            "spot": spot,
        })

    agg = {}
    for p in positions:
        u = p["underlying"]
        a = agg.setdefault(u, {"underlying": u, "spot": spots.get(u), "held_shares": held_u.get(u, 0.0),
                               "long_call_contracts": 0.0, "short_call_contracts": 0.0,
                               "long_put_contracts": 0.0, "short_put_contracts": 0.0,
                               "premium": 0.0, "notional": 0.0, "bullish_notional": 0.0,
                               "bearish_notional": 0.0, "put_assignment_cash": 0.0})
        c = abs(p["contracts"])
        if p["type"] == "call":
            a["long_call_contracts" if p["long"] else "short_call_contracts"] += c
        else:
            a["long_put_contracts" if p["long"] else "short_put_contracts"] += c
            if not p["long"]:
                a["put_assignment_cash"] += p["notional"]
        a["premium"] += p["premium"]
        a["notional"] += p["notional"]
        if (p["type"] == "call") == p["long"]:   # long call or short put -> bullish
            a["bullish_notional"] += p["notional"]
        else:
            a["bearish_notional"] += p["notional"]
    # covered/naked computed per (account, underlying) -- a short call is covered only by SAME-account shares
    cover_u = {}
    for (a_acct, u), sc in short_calls_au.items():
        sh = shares_au.get((a_acct, u), 0.0)
        cov = cover_u.setdefault(u, {"covered": 0.0, "naked": 0.0})
        cov["covered"] += min(sc, sh / 100.0)
        cov["naked"] += max(0.0, sc - sh / 100.0)
    for a in agg.values():
        cov = cover_u.get(a["underlying"], {"covered": 0.0, "naked": 0.0})
        a["covered_contracts"], a["naked_contracts"] = cov["covered"], cov["naked"]
        b, r = a["bullish_notional"], a["bearish_notional"]
        a["bias"] = "bullish" if b > r else ("bearish" if r > b else "neutral")

    positions.sort(key=lambda p: (-p["notional"], p["underlying"], p["strike"]))
    by_underlying = sorted(agg.values(), key=lambda a: -a["notional"])
    summary = {
        "n_positions": len(positions),
        "n_underlyings": len(by_underlying),
        "total_premium": sum(p["premium"] for p in positions),
        "long_premium_at_risk": sum(p["premium"] for p in positions if p["long"]),
        "short_credit": sum(p["premium"] for p in positions if not p["long"]),
        "total_notional": sum(p["notional"] for p in positions),
        "bullish_notional": sum(a["bullish_notional"] for a in by_underlying),
        "bearish_notional": sum(a["bearish_notional"] for a in by_underlying),
        "total_put_assignment_cash": sum(a["put_assignment_cash"] for a in by_underlying),
        "has_short": any(not p["long"] for p in positions),
        "has_naked_calls": any(a["naked_contracts"] > 1e-9 for a in by_underlying),
        "n_expired_excluded": n_expired_excluded,
    }
    return positions, by_underlying, summary


def expiration_calendar(lots, as_of, within=None, account=None):
    """Option expiration & assignment calendar (informational, NOT investment advice).

    Rows (one per dated option lot, sorted by expiry) carry days-to-expiry, premium at risk (long
    current value), short-put assignment cash (strike*100*abs(contracts)), and moneyness (from a spot
    derived from a held stock lot, else "n/a"). ``within`` keeps only options expiring within N days.
    Reuses ``parse_option``/``underlying_spots`` from the options node."""
    acct = (account or "").lower()
    spots = underlying_spots(lots)
    rows = []
    for lot in lots:
        po = parse_option(lot)
        if po is None or po["expiry"] is None:
            continue
        if acct and acct not in (lot.get("account") or "").lower():
            continue
        days = (po["expiry"] - as_of).days
        if within is not None and days > within:
            continue
        try:
            premium = float(lot.get("current_value"))
        except (TypeError, ValueError):
            premium = 0.0
        contracts = po["contracts"]
        notional = po["strike"] * po["multiplier"] * abs(contracts)
        is_short_put = po["type"] == "put" and not po["long"]
        rows.append({
            "expiry": po["expiry"].isoformat(), "days": days, "account": lot.get("account"),
            "underlying": po["underlying"], "type": po["type"], "strike": po["strike"],
            "contracts": contracts, "long": po["long"], "premium": premium,
            "premium_at_risk": premium if po["long"] else 0.0,
            "assignment_cash": notional if is_short_put else 0.0,
            "moneyness": _moneyness(po["type"], po["strike"], spots.get(po["underlying"])),
        })
    rows.sort(key=lambda r: (r["expiry"], r["underlying"], r["strike"]))
    win = within if within is not None else 30
    live = [r for r in rows if r["days"] >= 0]     # not-yet-expired rows drive the live/soon metrics
    summary = {
        "n": len(rows),
        "nearest_expiry": live[0]["expiry"] if live else None,
        "nearest_days": live[0]["days"] if live else None,
        "total_premium_at_risk": sum(r["premium_at_risk"] for r in live),
        "total_assignment_cash": sum(r["assignment_cash"] for r in live),
        "expired_assignment_cash": sum(r["assignment_cash"] for r in rows if r["days"] < 0),
        "n_itm": sum(1 for r in live if r["moneyness"] == "ITM"),
        "n_expiring_soon": sum(1 for r in live if r["days"] <= win),
        "soon_premium_at_risk": sum(r["premium_at_risk"] for r in live if r["days"] <= win),
        "window": within,
        "expired": sum(1 for r in rows if r["days"] < 0),
    }
    return rows, summary


def dividend_income(history, year=None):
    """Aggregate cash dividend income from a Fidelity Accounts-History export (informational, NOT tax
    advice). Sums the ``amount`` of ``DIVIDEND`` actions, optionally only those in calendar ``year``.

    Returns ``{total, by_symbol, by_account, n}`` where the two breakdowns are lists of
    ``{symbol|account, amount}`` (by_symbol sorted by amount desc, by_account by name). Qualified vs
    ordinary dividends are NOT distinguished (a Fidelity history export does not carry that flag)."""
    total = 0.0
    by_symbol, by_account = {}, {}
    n = 0
    for rec in history:
        if rec.get("action_kind") != "DIVIDEND":
            continue
        d = rec.get("date")
        if year is not None and (d is None or d.year != year):
            continue
        try:
            amt = float(rec.get("amount"))
        except (TypeError, ValueError):
            continue
        total += amt
        sym = (rec.get("symbol") or "").strip() or "(cash)"
        acct = (rec.get("account") or "").strip()
        by_symbol[sym] = by_symbol.get(sym, 0.0) + amt
        by_account[acct] = by_account.get(acct, 0.0) + amt
        n += 1
    return {
        "total": total,
        "by_symbol": [{"symbol": k, "amount": round(v, 2)}
                      for k, v in sorted(by_symbol.items(), key=lambda kv: (-kv[1], kv[0]))],
        "by_account": [{"account": k, "amount": round(v, 2)} for k, v in sorted(by_account.items())],
        "n": n,
    }
