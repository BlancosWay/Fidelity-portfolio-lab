#!/usr/bin/env python3
"""Loader for a Fidelity "Accounts History" transaction CSV (stdlib only, read-only).

`load_history(path)` returns a list of normalized transaction dicts, skipping the header preamble and
the trailing footer/disclaimer rows. Used by the wash-sale guardrail to find replacement purchases.

Columns of interest: Run Date (MM-DD-YYYY), Account, Account Number, Action, Symbol, Price, Quantity
(signed; sells are negative), Amount. Actions are classified into BUY | SELL | REINVEST | DIVIDEND |
OPTION_OPEN | OPTION_CLOSE | OTHER.
"""
import csv

from common import parse_us_date, parse_money, parse_qty
from tax_tools import security_key

# Acquisition events that count as wash-sale "replacement" purchases of a security.
BUY_KINDS = {"BUY", "REINVEST"}
# Disposition events.
SELL_KINDS = {"SELL"}


def classify_action(action):
    """Map a Fidelity Action string to a normalized kind.

    ``ACQUIRE_INFERRED`` covers non-BUY routes that MAY re-acquire a position — option
    assignment/exercise and inbound transfers/exchanges/journals. These are less certain than an
    explicit purchase, so ``_is_acquisition`` only counts them when shares actually came in
    (``signed_qty > 0``) and the wash-sale guardrail caps their severity at REVIEW."""
    a = (action or "").upper()
    if a.startswith("REINVESTMENT"):
        return "REINVEST"
    if a.startswith("DIVIDEND"):
        return "DIVIDEND"
    is_opt = ("CALL" in a) or ("PUT" in a) or ("OPTION" in a)
    if "OPENING" in a and is_opt:
        return "OPTION_OPEN"
    if "CLOSING" in a and is_opt:
        return "OPTION_CLOSE"
    if a.startswith("YOU BOUGHT"):
        return "BUY"
    if a.startswith("YOU SOLD"):
        return "SELL"
    if ("EXERCISED" in a) or ("ASSIGNED" in a) or ("TRANSFER" in a) or ("EXCHANGE" in a) \
            or ("JOURNAL" in a):
        return "ACQUIRE_INFERRED"   # possible replacement via a non-BUY route; verified by qty + REVIEW
    return "OTHER"


def load_history(path):
    """Parse a Fidelity history CSV into normalized transaction dicts.

    Rows whose Run Date does not parse (blank lines, the footer number, "Date downloaded ...") are
    skipped, so the loader tolerates the export's trailing disclaimer rows."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        raw = fh.read().splitlines()
    hdr_i = None
    for i, line in enumerate(raw):
        if line.lstrip('"').startswith("Run Date"):
            hdr_i = i
            break
    if hdr_i is None:
        raise ValueError("history CSV: no 'Run Date' header row found")

    records = []
    for row in csv.DictReader(raw[hdr_i:]):
        date = parse_us_date(row.get("Run Date", ""))
        if date is None:
            continue  # footer / blank / disclaimer row
        symbol = (row.get("Symbol") or "").strip()
        sk = security_key(symbol)
        signed_qty = parse_qty(row.get("Quantity"))
        records.append({
            "date": date,
            "account": (row.get("Account") or "").strip(),
            "account_number": (row.get("Account Number") or "").strip(),
            "action": (row.get("Action") or "").strip(),
            "action_kind": classify_action(row.get("Action") or ""),
            "symbol": symbol,
            "sec_key": sk["key"],
            "kind": sk["kind"],
            "underlying": sk["underlying"],
            "signed_qty": signed_qty,
            "abs_qty": abs(signed_qty),
            "price": parse_money(row.get("Price")),
            "amount": parse_money(row.get("Amount")),
        })
    return records
