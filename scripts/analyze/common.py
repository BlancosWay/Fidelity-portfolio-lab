#!/usr/bin/env python3
"""Shared stdlib parsing/date helpers for the Fidelity-portfolio-lab analyzer.

Extracted from ``portfolio.py`` so ``portfolio.py``, ``history.py`` and ``tax_tools.py`` can all
reuse them without circular imports. ``portfolio.py`` re-imports these names, so existing references
(e.g. ``portfolio.holding_term``) keep working.
"""
import datetime as dt
import re

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_money(s):
    """Parse '$1,425.00', '+64.40%', '-$900.00', '($5.00)' -> float (or None)."""
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").replace("+", "").replace("%", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_qty(s):
    if s is None:
        return 0.0
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return 0.0


def parse_date(s):
    """Parse a positions 'Date Acquired' like 'Mmm-DD-YYYY' (e.g. Mar-11-2026); also tolerate
    YYYY-MM-DD and MM/DD/YYYY. Returns a datetime.date or None."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^([A-Za-z]{3})[-\s](\d{1,2})[-,\s]+(\d{4})$", s)
    if m and m.group(1).lower() in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_us_date(s):
    """Parse a Fidelity history 'Run Date' like '07-02-2026' (MM-DD-YYYY); also tolerate M-D-YYYY
    and MM/DD/YYYY. Returns a datetime.date or None (so footer/blank rows are skipped by the caller)."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if not m:
        return None
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(yy, mm, dd)
    except ValueError:
        return None


def one_year_anniversary(d):
    """One-year calendar anniversary; a Feb-29 date clamps to Feb-28 of the next year."""
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb-29 -> the next year has no Feb-29
        return dt.date(d.year + 1, 2, 28)


def holding_term(acquired, as_of):
    """Long-Term iff as_of is strictly after the one-year anniversary; else Short-Term.

    Returns None when the acquisition date is unknown (e.g. cash rows)."""
    if acquired is None:
        return None
    return "Long-Term" if as_of > one_year_anniversary(acquired) else "Short-Term"
