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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "portfolio.db")

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

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
    """Parse 'Mmm-DD-YYYY' (e.g. Mar-11-2026); also tolerate YYYY-MM-DD and MM/DD/YYYY."""
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


def one_year_anniversary(d):
    """One-year calendar anniversary; a Feb-29 date clamps to Feb-28 of the next year."""
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb-29 -> the next year has no Feb-29
        return dt.date(d.year + 1, 2, 28)


def holding_term(acquired, as_of):
    """Long-Term iff as_of is strictly after the one-year anniversary; else Short-Term."""
    if acquired is None:
        return None
    return "Long-Term" if as_of > one_year_anniversary(acquired) else "Short-Term"


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


# --------------------------------------------------------------------------- load
def load(csv_path, db_path=DEFAULT_DB, as_of=None):
    as_of = as_of or dt.date.today()
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        missing = [h for h in EXPECTED_HEADERS if h not in headers]
        if missing:
            raise ValueError(f"CSV missing expected columns: {missing}")
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
    conn = readonly_connection(db_path)
    try:
        return conn.execute(stmt).fetchall()
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


def summary(db_path):
    conn = _connect(db_path)
    print("== Units per symbol across ALL accounts ==")
    rows = conn.execute(
        """SELECT symbol, ROUND(SUM(quantity),4) units, COUNT(*) lots,
                  COUNT(DISTINCT account) accts,
                  ROUND(SUM(CASE WHEN term='Long-Term'  THEN quantity ELSE 0 END),4) long_units,
                  ROUND(SUM(CASE WHEN term='Short-Term' THEN quantity ELSE 0 END),4) short_units
           FROM lots GROUP BY symbol ORDER BY symbol""").fetchall()
    _print_table(["Symbol", "Units", "Lots", "#Accts", "Long(>1yr)", "Short(<=1yr)"], [tuple(r) for r in rows])

    print("\n== Long vs Short (whole portfolio) ==")
    rows = conn.execute(
        """SELECT term, COUNT(*) lots, ROUND(SUM(current_value),2) market_value
           FROM lots WHERE term IN ('Long-Term','Short-Term') GROUP BY term ORDER BY term""").fetchall()
    _print_table(["Term", "Lots", "Market Value"], [tuple(r) for r in rows])

    print("\n== Per account by term ==")
    rows = conn.execute(
        """SELECT account,
                  SUM(CASE WHEN term='Long-Term'  THEN 1 ELSE 0 END) long_lots,
                  SUM(CASE WHEN term='Short-Term' THEN 1 ELSE 0 END) short_lots,
                  ROUND(SUM(current_value),2) market_value
           FROM lots GROUP BY account ORDER BY account""").fetchall()
    _print_table(["Account", "Long lots", "Short lots", "Market Value"], [tuple(r) for r in rows])
    conn.close()


def symbol_detail(db_path, sym):
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT account, quantity, date_acquired, term, current_value FROM lots WHERE symbol=? ORDER BY date_acquired",
        (sym,)).fetchall()
    if not rows:
        print(f"No lots for symbol {sym!r}")
        conn.close()
        return
    _print_table(["Account", "Quantity", "Acquired", "Term", "Current Value"], [tuple(r) for r in rows])
    t = conn.execute(
        """SELECT ROUND(SUM(quantity),4) u,
                  ROUND(SUM(CASE WHEN term='Long-Term'  THEN quantity ELSE 0 END),4) lu,
                  ROUND(SUM(CASE WHEN term='Short-Term' THEN quantity ELSE 0 END),4) su
           FROM lots WHERE symbol=?""", (sym,)).fetchone()
    print(f"\nTotal {sym}: {t['u']} units ({t['lu']} long, {t['su']} short)")
    conn.close()


def accounts_list(db_path):
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT account, COUNT(*) lots, COUNT(DISTINCT symbol) symbols FROM lots GROUP BY account ORDER BY account").fetchall()
    _print_table(["Account", "Lots", "Symbols"], [tuple(r) for r in rows])
    conn.close()


# --------------------------------------------------------------------------- CLI
def main(argv=None):
    p = argparse.ArgumentParser(prog="portfolio", description="Analyze Fidelity lot exports (read-only).")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite DB path (default: {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("load", help="load a lots CSV into the DB")
    lp.add_argument("csv")
    lp.add_argument("--as-of", help="YYYY-MM-DD term-as-of date (default today)")
    sub.add_parser("summary", help="print standard aggregations")
    sp = sub.add_parser("symbol", help="detail for one symbol")
    sp.add_argument("sym")
    sub.add_parser("accounts", help="list accounts")
    qp = sub.add_parser("query", help="run a read-only SELECT over the lots table")
    qp.add_argument("sql")
    args = p.parse_args(argv)

    if args.cmd == "load":
        as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
        print(f"Loaded {load(args.csv, args.db, as_of)} lots into {args.db}")
    elif args.cmd == "summary":
        summary(args.db)
    elif args.cmd == "symbol":
        symbol_detail(args.db, args.sym)
    elif args.cmd == "accounts":
        accounts_list(args.db)
    elif args.cmd == "query":
        rows = run_query(args.db, args.sql)
        if rows:
            _print_table(list(rows[0].keys()), [tuple(r) for r in rows])
        print(f"({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
