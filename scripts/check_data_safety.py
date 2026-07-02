#!/usr/bin/env python3
"""Fail if any tracked file could leak real Fidelity holdings (stdlib only).

Enforces the "never commit real data" guarantee across the whole repo:
  1. Only the synthetic `tests/sample_lots.csv` may be a tracked .csv/.tsv; no .db/.sqlite tracked.
  2. No tracked text file may contain a Fidelity-style account identifier (Z + 8 digits), and no
     tracked CSV/TSV may contain a bare 9-digit brokerage-style number.

Run: python scripts/check_data_safety.py   (exit 0 = clean, 1 = problems found)
"""
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOWED_CSV = {"tests/sample_lots.csv"}
# The scanner and its own test legitimately contain example account ids; skip them in the id scan.
ID_SCAN_SKIP = {"scripts/check_data_safety.py", "tests/test_data_safety.py"}

# Spreadsheet export formats that must never be committed (Excel variants, OpenDocument, Numbers).
SPREADSHEET_EXTS = (".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".numbers")

FIDELITY_ACCT = re.compile(r"\bZ\d{8}\b")          # e.g. Z05596750
NINE_DIGITS = re.compile(r"\b\d{9}\b")             # bare brokerage-style number (CSV only)


def disallowed_path(rel):
    """Return a problem string if this tracked path must never be committed, else None."""
    low = rel.lower()
    if low.endswith((".csv", ".tsv")) and rel not in ALLOWED_CSV:
        return f"{rel}: exported data file must not be committed (only {sorted(ALLOWED_CSV)} allowed)"
    if low.endswith(SPREADSHEET_EXTS):
        return f"{rel}: spreadsheet export must not be committed"
    if low.endswith((".db", ".sqlite", ".sqlite3")):
        return f"{rel}: database file must not be committed"
    return None


def scan_text(rel, text):
    """Return a list of problem strings for account-identifier leakage in `text`."""
    problems = [f"{rel}: contains a Fidelity-style account id {m!r}" for m in FIDELITY_ACCT.findall(text)]
    if rel.lower().endswith((".csv", ".tsv")):
        problems += [f"{rel}: CSV contains a 9-digit account-like number {m!r}" for m in NINE_DIGITS.findall(text)]
    return problems


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def check(files, read_text):
    problems = []
    for rel in files:
        bad = disallowed_path(rel)
        if bad:
            problems.append(bad)
    for rel in files:
        if rel in ID_SCAN_SKIP:
            continue
        try:
            problems += scan_text(rel, read_text(rel))
        except OSError:
            continue
    return problems


def _read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def main():
    files = tracked_files()
    problems = check(files, _read)
    if problems:
        print("DATA SAFETY CHECK FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print(f"Data safety OK: scanned {len(files)} tracked files; only synthetic sample data present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
