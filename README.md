# Fidelity-portfolio-lab

[![Validate](https://github.com/BlancosWay/Fidelity-portfolio-lab/actions/workflows/validate.yml/badge.svg)](https://github.com/BlancosWay/Fidelity-portfolio-lab/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.x-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none%20(stdlib)-brightgreen)

Compliant, **read-only**, **local** analysis of your Fidelity holdings by tax lot.

> **Not affiliated with Fidelity.** No API, no credentials, no login automation. See [NOTICE](NOTICE).

Fidelity has no retail API, so this tool never touches your credentials or automates login. Instead:

1. You paste a **read-only browser console script** into your own logged-in Fidelity session; it
   reads the page and downloads a `fidelity_lots.csv`.
2. A small **stdlib-only Python analyzer** loads that CSV into a local SQLite table and answers
   questions: units per symbol across all accounts, **long-term (>1yr) vs short-term (<=1yr)** by
   holding period, per-account breakdowns, and arbitrary read-only SQL.

Nothing leaves your machine; no exported holdings are ever committed to git.

## Layout
```
scripts/browser/fidelity_lot_export.js     # read-only exporter (paste into Fidelity console)
scripts/browser/fidelity_dom_inspector.js  # read-only DOM diagnostic (maintenance)
scripts/analyze/portfolio.py               # SQLite-backed analyzer CLI
tests/                                      # stdlib unittest (analyzer + browser-safety scan)
data/                                       # your exports + DB land here (git-ignored)
SKILL.md                                    # Copilot skill definition
```

## Quickstart

Try it against the bundled synthetic sample (no Fidelity needed):
```
python scripts/analyze/portfolio.py --db data/portfolio.db load tests/sample_lots.csv --as-of 2026-07-01
python scripts/analyze/portfolio.py --db data/portfolio.db summary
```

Real data:
1. Fidelity → **Positions** (All accounts).
2. Console (**Ctrl+Shift+J**); if prompted, type `allow pasting`.
3. Paste all of `scripts/browser/fidelity_lot_export.js`, Enter. It **auto-expands collapsed account
   groups**, scrapes every lot, and downloads `fidelity_lots.csv`.
4. `python scripts/analyze/portfolio.py load path/to/fidelity_lots.csv`
5. `python scripts/analyze/portfolio.py summary` (or `symbol <SYM>`, `accounts`, or `query "SELECT ..."`).

## Commands
| Command | Purpose |
|---|---|
| `load <csv> [--as-of YYYY-MM-DD]` | Load an export; recompute term as-of a date (default today). |
| `summary` | Units per symbol across accounts; long vs short; per-account by term. |
| `symbol <SYM>` | Per-lot detail + totals for one symbol. |
| `accounts` | Accounts overview. |
| `query "<SELECT ...>"` | Ad-hoc **read-only** SQL over the `lots` table. |
| `harvest [--as-of D] [--st-rate R] [--lt-rate R]` | Tax-loss harvest candidates (taxable accounts, short-term first). |
| `ripening [--within N] [--as-of D] [--st-rate R] [--lt-rate R]` | Taxable short-term lots and the date each becomes long-term. |
| `concentration [--top N] [--threshold P]` | Cross-account concentration by symbol + Herfindahl index. |
| `sell <SYM> <SHARES> [--strategy S] [--account A] [--as-of D] [--st-rate R] [--lt-rate R]` | Pick specific **taxable** lots to sell (`hifo`/`fifo`/`loss-first`/`min-tax`); tax-advantaged lots excluded. |
| `washsale <history.csv> [--as-of D] [--window N] [--same-underlying]` | Flag a taxable loss whose security was bought near the sale in any account. |
| `capacity [--income X] [--ceiling X] [--ceiling-label L] [--target-gain X] [--within-rate R] [--account A] [--as-of D] [--lt-rate R]` | Which taxable long-term gain lots to realize to fill a 0% LTCG (or other) headroom, or a `--target-gain`. |
| `gift [--min-gain-pct P] [--top N] [--account A] [--as-of D] [--lt-rate R]` | Rank taxable long-term appreciated lots as charitable-donation candidates. |
| `dashboard [--within N] [--income X] [--ceiling X] [--as-of D] [--st-rate R] [--lt-rate R]` | Year-end snapshot: unrealized ST/LT by account, harvestable losses, ripening, liquidation tax, 0% LTCG capacity. |
| `options [--account A] [--as-of D] [--top N]` | Options exposure: premium at risk, notional, moneyness (ITM/OTM), per-underlying directional bias, covered/naked. |
| `expiration [--within N] [--account A] [--as-of D] [--top N]` | Option expiration & assignment calendar: days-to-expiry, premium at risk by expiry, moneyness, short-put assignment cash. |

> `--db PATH` is a **global** option — place it *before* the subcommand (default `data/portfolio.db`),
> e.g. `python scripts/analyze/portfolio.py --db data/portfolio.db summary`. `--as-of YYYY-MM-DD`
> (default today) applies to every tax subcommand (`harvest`/`ripening`/`sell`/`washsale`/`capacity`/
> `gift`/`dashboard`); `--st-rate`/`--lt-rate` (defaults `0.32`/`0.15`) tune the labeled estimates on the
> commands that accept them (plus `capacity`'s `--within-rate`).
>
> The tax tools are **read-only** and every dollar/tax figure is an **estimate, not tax advice**.
> `harvest`/`ripening` cover taxable accounts only — any account whose name matches
> IRA/Roth/HSA/BrokerageLink/401k/403b/529 is treated as tax-advantaged and excluded, and every other
> account is treated as taxable. `washsale` needs a Fidelity **Accounts History** CSV and only sees the
> window you export (so `CLEAN` is not a guarantee): for each current taxable loss it flags a
> same-security purchase in the **prior `--window` days through `--as-of`** in *any* account —
> **BLOCKED** for an IRA/Roth/HSA buy (permanent disallowance, e.g. Rev. Rul. 2008-5 for IRAs),
> **REVIEW** for a 401(k)/403(b)/BrokerageLink/529 buy (no IRS guidance; prevailing view is the rule
> does **not** apply), else **CAUTION** for another taxable account — plus a forward "don't repurchase
> within N days" reminder and a ±`--window` audit of past sells.
>
> `capacity` selects taxable **long-term gain** lots (largest gain first, final lot taken partially)
> to realize either a `--target-gain` or the headroom `max(0, --ceiling − --income)` to an income
> ceiling you supply. `--within-rate` (default `0.0`) is the marginal LTCG rate on gains realized
> below the ceiling: `0.0` models the **0% long-term bracket** (tax-free); pass your real LTCG rate
> for an **NIIT/IRMAA** ceiling, which only avoids the surcharge/tier while the gain is still taxed.
>
> `gift` ranks taxable **long-term appreciated** lots (highest gain% first) as charitable-donation
> candidates — donating appreciated long-term shares avoids the capital-gains tax and (if you itemize)
> deducts fair market value — and counts short-term-gain and loss lots separately (wait / harvest
> instead). `--min-gain-pct` (a percent number, e.g. `20`) filters to the most-appreciated lots.
>
> `dashboard` is a read-only year-end snapshot that consolidates the other tools: unrealized ST/LT
> gain/loss by account (taxable vs tax-advantaged), harvestable losses, lots ripening within
> `--within` days, the estimated tax if all taxable lots were sold now, and — with `--income`/`--ceiling`
> — the 0% LTCG realization capacity.
>
> `options` parses each option lot (`AAL 17 Call` + the expiry from the Description column) into
> underlying/strike/type/expiry and reports **premium at risk** (current value), **notional**
> (strike×100×contracts), long/short (by quantity sign), per-underlying directional bias, and — for any
> *short* options — covered-vs-naked calls and cash-secured-put assignment cash. **Moneyness (ITM/OTM)**
> uses a spot derived from your largest held stock lot for that underlying (approximate — verify against
> your broker), and is "n/a" when the underlying isn't held as stock. Delta/theta/IV are **not** computed
> (they need live quotes; this tool is offline). Informational — **not investment advice**.
>
> `expiration` is the option **expiration & assignment calendar**: one row per dated option lot sorted by
> expiry, with days-to-expiry, premium at risk (long current value), moneyness, and — for any *short*
> puts — the assignment cash if assigned. `--within N` limits to options expiring within N days.
> Informational — **not investment advice**.

## Definitions
**long** = held **> 1 year**; **short** = held **<= 1 year** (exactly one year counts as short),
computed from each lot's acquisition date. A Feb-29 acquisition uses Feb-28 as its one-year
anniversary (Mar-1 is its first long-term day).

## Safety
- The browser scripts are **read-only**: zero network calls, no credential/storage access, and they
  only click Fidelity's own lot-expand buttons plus a local download link. This is enforced
  statically by `tests/test_browser_safety.py`.
- `query` opens SQLite `mode=ro` with `PRAGMA query_only=ON` and accepts only a single `SELECT`/`WITH`.
- `.gitignore` keeps real exports (`data/`, `*.csv`, `*.db`) out of git; only `tests/sample_lots.csv`
  is tracked.

## Tests
```
python -m unittest discover -s tests
```
Or run the full local gate (tests + byte-compile + data-safety + `node --check` + release dry run):
```
python scripts/check.py
```

## Contributing & project docs
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev workflow and the safety ground rules.
- [SECURITY.md](SECURITY.md) — the safety model and how to report a vulnerability privately.
- [docs/REPO_SETUP.md](docs/REPO_SETUP.md) — the CI pipeline + branch-protection/approval setup.
- [RELEASING.md](RELEASING.md) · [CHANGELOG.md](CHANGELOG.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

Continuous integration runs on every push/PR: unit tests, byte-compile, a **data-safety** scan
(fails if any real export or account identifier is committed), `node --check` of the browser
scripts, a release dry run, and (on PRs) a changelog check. Releases are published from `main` by
pushing a `vX.Y.Z` tag.

## License
[MIT](LICENSE) © BlancosWay

## Disclaimer
Personal tooling, provided as-is. Term classifications are a convenience for organizing holdings and
are **not tax advice**; verify against your official Fidelity cost-basis documents.
