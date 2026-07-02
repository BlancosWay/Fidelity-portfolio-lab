---
name: fidelity-portfolio-lab
description: >-
  Compliant, read-only analysis of your Fidelity holdings by tax lot. Use when the user wants to
  export their Fidelity positions and aggregate them — units per symbol across all accounts,
  long-term (>1yr) vs short-term (<=1yr) by holding period, per-account breakdowns, or any ad-hoc
  query over their lots. No credentials, no login automation, no third-party services: data comes
  from a read-only browser console script the user runs in their own session, and all analysis runs
  locally on the exported CSV.
---

# Fidelity Portfolio Lab

Help the user export their Fidelity per-lot holdings **compliantly** and analyze them **locally**.

## Compliance (non-negotiable)
- Never ask for or handle Fidelity credentials; never automate login; never use third-party
  services/aggregators. Fidelity has no retail API, so the only compliant path is the user's own
  manual, read-only export.
- Data acquisition is a **read-only** browser console script the user pastes into their own
  already-authenticated Fidelity session. It makes zero network calls, only reads the DOM, clicks
  only Fidelity's own lot-expand buttons, and downloads a local CSV. This is enforced by
  `tests/test_browser_safety.py`.

## Workflow

### 1. Export (the user runs this in their browser)
Guide the user to:
1. Open Fidelity → **Positions** (All accounts is fine).
2. Open the DevTools console (**Ctrl+Shift+J**); if prompted, type `allow pasting`.
3. Paste the entire contents of **`scripts/browser/fidelity_lot_export.js`** and press Enter.
4. It **auto-expands any collapsed account groups**, then expands each position one at a time
   (~1-2 min for large accounts), prints per-symbol and per-account summaries, and downloads
   **`fidelity_lots.csv`** (usually to `~/Downloads`), then collapses everything back.

   (If your positions are already listed you needn't do anything first. If auto-expand ever
   can't find the control, manually expand the **"Account:"** groups and re-run.)

If it reports **0 lots** (Fidelity changed its Positions UI), have the user run
`scripts/browser/fidelity_dom_inspector.js` and share the downloaded `fidelity_dom_report.txt` so the
export selectors can be updated.

### 2. Load
Move the exported CSV into the repo's git-ignored `data/` folder (or just use its Downloads path):
```
python scripts/analyze/portfolio.py load path/to/fidelity_lots.csv
```
- `--as-of YYYY-MM-DD` sets the date used to classify long vs short (default: today).
- `--db PATH` is a **global** option — place it *before* the subcommand (e.g.
  `python scripts/analyze/portfolio.py --db data/portfolio.db load ...`); default `data/portfolio.db`
  (git-ignored).

### 3. Analyze
```
python scripts/analyze/portfolio.py summary       # units/symbol across accounts; long vs short; per account
python scripts/analyze/portfolio.py symbol AAPL   # per-lot detail + totals for one symbol
python scripts/analyze/portfolio.py accounts      # accounts overview
python scripts/analyze/portfolio.py query "SELECT symbol, ROUND(SUM(quantity),4) FROM lots GROUP BY symbol ORDER BY 2 DESC"
```
Use **`query`** for any ad-hoc question the user asks — it is **read-only** (opens the DB
`mode=ro` + `PRAGMA query_only`, accepts a single `SELECT`/`WITH` statement only). Translate the
user's question into SQL over the `lots` table.

## Definitions
- **long** = held **> 1 year** (long-term); **short** = held **<= 1 year** (short-term). Computed
  from each lot's acquisition date; **exactly one year = short**. A Feb-29 acquisition uses Feb-28 as
  its anniversary, so Mar-1 is its first long-term day.

## Data model — table `lots`
`account, symbol, description, margin_cash, quantity, date_acquired (ISO), term_fidelity,
avg_cost_basis, cost_basis_total, current_value, gain_loss, gain_loss_pct, term`
where `term` is the authoritatively recomputed `Long-Term`/`Short-Term` (the CSV's own preview
column is ignored).

## Safety & maintenance
- Real exports are sensitive — keep them in `data/` (git-ignored); never commit them. Only the
  synthetic `tests/sample_lots.csv` is tracked.
- Run the tests with `python -m unittest discover -s tests`.
- To extend: add a new subcommand in `scripts/analyze/portfolio.py` or just use `query`.
