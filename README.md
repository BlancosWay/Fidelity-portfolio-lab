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
1. Fidelity → **Positions** (All accounts). Click **"Expand groups"** so positions are listed.
2. Console (**Ctrl+Shift+J**); if prompted, type `allow pasting`.
3. Paste all of `scripts/browser/fidelity_lot_export.js`, Enter. It downloads `fidelity_lots.csv`.
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

> `--db PATH` is a **global** option — place it *before* the subcommand (default `data/portfolio.db`),
> e.g. `python scripts/analyze/portfolio.py --db data/portfolio.db summary`.

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
