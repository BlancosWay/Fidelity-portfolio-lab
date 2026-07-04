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

### 4. Tax / portfolio tools (Tier-1)
All read-only; every dollar/tax figure is an **estimate, not tax advice**. Rate flags `--st-rate`
(default `0.32`) and `--lt-rate` (default `0.15`) only affect the labeled estimates.
```
python scripts/analyze/portfolio.py harvest                       # tax-loss harvest candidates (taxable accounts, short-term first)
python scripts/analyze/portfolio.py ripening --within 60          # short-term lots about to become long-term
python scripts/analyze/portfolio.py concentration --top 15        # cross-account concentration + HHI
python scripts/analyze/portfolio.py sell AAPL 50 --strategy min-tax   # which specific lots to sell (hifo|fifo|loss-first|min-tax)
python scripts/analyze/portfolio.py washsale path/to/Accounts_History.csv --same-underlying
python scripts/analyze/portfolio.py capacity --income 40000 --ceiling 50000   # 0% LTCG gain-harvest headroom
python scripts/analyze/portfolio.py gift --min-gain-pct 20          # appreciated long-term lots best to donate
python scripts/analyze/portfolio.py dashboard --within 60          # year-end tax snapshot (all tools consolidated)
python scripts/analyze/portfolio.py options --top 15               # options exposure by underlying (premium, notional, moneyness)
python scripts/analyze/portfolio.py expiration --within 30         # options expiring within 30 days (premium at risk, moneyness)
```
- **`harvest`** — taxable accounts only; excludes tax-advantaged (IRA/Roth/HSA/BrokerageLink/529) and
  cash; ranks short-term losses first (they offset ordinary income). The estimated benefit models
  single-year capital-loss netting: losses first offset realized gains (`--offsetting-st-gains`/
  `--offsetting-lt-gains`), then up to $3,000 of ordinary income, with the rest carried forward.
- **`ripening`** — taxable short-term lots and the exact date each becomes long-term; flags short-term
  *losers* to harvest before they ripen.
- **`concentration`** — aggregates value by symbol across all accounts (cash reported separately);
  Herfindahl index + single-name flags (`--threshold`, default `0.05`).
- **`sell SYMBOL SHARES`** — picks the specific **taxable** lots to sell to minimize tax and prints the
  specific-ID instruction plus the delta vs FIFO (`--account` restricts to matching accounts).
  Tax-advantaged lots (IRA/Roth/HSA/BrokerageLink/529) are excluded (their gains are tax-free), and a
  pick spanning accounts prints a per-account NOTE (specific-ID sales are one order per account).
- **`washsale HISTORY.csv`** — needs a Fidelity **Accounts History** CSV export. For each current
  taxable loss it flags a same-security purchase in the **prior `--window` days (through `--as-of`)**
  in *any* account, graded by the buying account: **BLOCKED** for an IRA/Roth/HSA (loss permanently
  disallowed — Rev. Rul. 2008-5 for IRAs), **REVIEW** for a 401(k)/403(b)/BrokerageLink/529 (no IRS
  wash-sale guidance; prevailing view is the rule does **not** apply — confirm with a tax pro), else
  **CAUTION** for another taxable account; it also prints a forward "don't repurchase within N days"
  reminder and a ±`--window` audit of past sells. Limitation: it only sees the history window you
  export, so `CLEAN` is not a guarantee.
- **`capacity`** — bracket-aware realized-gain planner over taxable **long-term gain** lots. Fills
  either a `--target-gain` or the headroom `max(0, --ceiling − --income)` to an income ceiling you
  supply, selecting the biggest-gain lots first (final lot taken partially). `--within-rate` (default
  `0.0`) is the marginal LTCG rate on gains below the ceiling: `0.0` = the **0% long-term bracket**
  (tax-free); pass your real LTCG rate for an **NIIT/IRMAA** ceiling (avoids the surcharge/tier, but
  the gain is still taxed). Estimates only, **not tax advice**.
- **`gift`** — appreciated-lot donor picker. Ranks taxable **long-term** gain lots by gain%
  (most-appreciated first) as charitable-donation candidates, with the est. cap-gains tax avoided
  (`--min-gain-pct` filters, e.g. `20`); short-term-gain and loss lots are counted and steered
  elsewhere (wait for long-term / harvest instead). Estimates only, **not tax advice** — the FMV
  deduction depends on itemizing and AGI limits.
- **`dashboard`** — read-only year-end tax snapshot consolidating the other tools: unrealized ST/LT
  gain/loss by account (taxable vs tax-advantaged), harvestable losses, lots ripening within
  `--within` days, the estimated tax if all taxable lots were sold now (ST and LT netted; a net loss
  shows the $3,000-capped current-year benefit plus carryforward), and — with `--income`/`--ceiling`
  — the 0% LTCG realization capacity. Estimates only, **not tax advice**.
- **`options`** — options exposure dashboard. Parses each option lot (`AAL 17 Call` + the expiry from
  the Description column) into underlying/strike/type/expiry; reports premium at risk (current value),
  notional (strike×100×contracts), long/short (by quantity sign), per-underlying directional bias, and
  covered-vs-naked / cash-secured-put assignment cash for any short options. Moneyness (ITM/OTM) uses a
  spot from your largest held stock lot per underlying (approximate; "n/a" when not held). Delta/theta
  need live quotes and are not computed. Already-expired contracts are excluded from exposure. **Not
  investment advice.**
- **`expiration`** — option expiration & assignment calendar: one row per dated option lot sorted by
  expiry, with days-to-expiry, premium at risk (long current value), moneyness, and short-put
  assignment cash. `--within N` limits to options expiring within N days. Already-expired contracts are
  still listed (with a separate count) but excluded from the live/soon/assignment totals. **Not
  investment advice.**

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
