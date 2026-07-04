# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- **Tier-3 options tools** — new read-only `portfolio.py` subcommands (stdlib only; informational, not
  investment advice):
  - `options` — options exposure dashboard. Parses each option lot (`AAL 17 Call` + expiry from the
    Description column) into underlying/strike/type/expiry; reports premium at risk (current value),
    notional (strike×100×contracts), long/short by quantity sign, per-underlying directional bias, and
    covered-vs-naked / cash-secured-put assignment cash for short options. Moneyness (ITM/OTM) uses a
    spot from the largest held stock lot per underlying (approximate; "n/a" when not held).
    Delta/theta are not computed (need live quotes).
  - `expiration` — option expiration & assignment calendar: one row per dated option lot sorted by
    expiry, with days-to-expiry, premium at risk (long), moneyness, and short-put assignment cash;
    `--within N` limits to options expiring within N days.
- **Tier-2 tax tools** — new read-only `portfolio.py` subcommands (stdlib only; estimates, not tax advice):
  - `capacity` — bracket-aware realized-gain capacity planner. Selects taxable long-term gain lots
    (biggest gain first, final lot partial) to fill a `--target-gain` or the headroom
    `max(0, --ceiling − --income)` to an income ceiling you supply. `--within-rate` (default `0.0`)
    is the marginal LTCG rate on gains below the ceiling: `0.0` = the 0% long-term bracket (tax-free);
    pass your real LTCG rate for an NIIT/IRMAA ceiling (avoids the surcharge/tier, gain still taxed).
  - `gift` — appreciated-lot donor picker. Ranks taxable long-term gain lots by gain% as
    charitable-donation candidates (donating appreciated long-term shares avoids the capital-gains
    tax and deducts FMV if you itemize), with the est. cap-gains tax avoided; short-term-gain and
    loss lots are counted and steered elsewhere. `--min-gain-pct` filters to the most-appreciated lots.
  - `dashboard` — read-only year-end tax snapshot consolidating the Tier-1/Tier-2 tools: unrealized
    ST/LT gain/loss by account (taxable vs tax-advantaged), harvestable losses, lots ripening within
    `--within` days, the estimated tax if all taxable lots were sold now, and (with `--income`/`--ceiling`)
    the 0% LTCG realization capacity.
- **Tier-1 tax/portfolio tools** — five new read-only `portfolio.py` subcommands (stdlib only; all
  dollar/tax figures are estimates, not tax advice):
  - `harvest` — tax-loss harvest candidates in taxable accounts, short-term losses first.
  - `washsale <history.csv>` — cross-account wash-sale guardrail using a Fidelity Accounts History
    CSV; for a current taxable loss, a same-security purchase in the prior `--window` days (through
    `--as-of`) in any account is graded by the buying account: **BLOCKED** for an IRA/Roth/HSA
    (permanent disallowance — Rev. Rul. 2008-5 for IRAs), **REVIEW** for a 401(k)/403(b)/BrokerageLink/529
    (no IRS wash-sale guidance; prevailing view is the rule does not apply), else **CAUTION** for
    another taxable account, plus a forward repurchase warning and a ±window realized-history audit.
    `--same-underlying` relates a stock loss to option buy-to-open on the same underlying.
  - `sell <SYM> <SHARES>` — specific-ID/HIFO lot selector (`hifo`/`fifo`/`loss-first`/`min-tax`) with
    the realized gain split ST/LT and the delta vs FIFO.
  - `ripening` — taxable short-term lots and the date each becomes long-term (harvest losers first).
  - `concentration` — cross-account value-by-symbol concentration, Herfindahl index, single-name flags.
  New stdlib modules `scripts/analyze/common.py` (shared parsers, incl. `parse_us_date`),
  `scripts/analyze/history.py` (transaction-history loader), and `scripts/analyze/tax_tools.py` (pure
  analysis logic), plus a read-only `portfolio.fetch_lots`. All analysis is local and read-only.
- The browser exporter now **auto-expands collapsed account groups** on the "All accounts" view
  (via Fidelity's own read-only "Expand groups" button), so all positions render before scraping —
  no manual "Expand groups" click required. A collapsed group is detected **structurally** (an
  "Account:" row with no position rows beneath it), not by the `ag-row-group-contracted` CSS class,
  which Fidelity leaves on rows even when they are expanded.
- Cash / core money-market balances are now included in the export. Each account's cash row
  (Fidelity's `posweb-row-core` "Cash … HELD IN MONEY MARKET" row, which has a balance but no tax
  lots) is written as a value-only CSV row (`Symbol=CASH`, `Current Value` set, date/term/lot fields
  blank). Detection matches the `posweb-row-core` class plus a "Cash" label, so tickers containing
  "core" (e.g. CoreWeave/CRWV) are never misread as cash. These rows are excluded from the long/short
  lot aggregation but count toward each account's market value.

### Fixed
- The exporter now captures **all** lots on large accounts (100+ positions). It reads positions
  **one at a time** (expand → read that drawer's lot table → collapse) instead of opening every
  drawer at once. Opening all drawers made the grid tall enough that Fidelity's AG Grid started
  virtualising (dropping) off-screen drawer rows, so later positions were silently truncated
  mid-way (e.g., a `BrokerageLink` account captured only symbols A–C). One-at-a-time keeps the DOM
  small so nothing is dropped; each position is addressed by its **ordinal** in the expander list
  (collision-proof when a symbol appears twice in one account) and its lots are read only from the
  drawer that newly appeared, so nothing is misattributed.
- The exporter now parses lots on accounts whose position drawer opens on the **Research** tab. It
  activates each drawer's **"Purchase history"** tab (Fidelity's own in-drawer `<button role="tab">`
  that controls the `posweb-drawer-tabpanel-lots` panel) so `table.posweb-purchase-history` renders
  before scraping. Previously such accounts produced "Parsed 0 lots" because the lot table was never
  rendered. Drawers already on Purchase history are left untouched (no-op), and the click routes
  through the runtime `safeClick` guard (the tab button has no `href` and never navigates).

### Changed
- Hardened the browser scripts' read-only guarantee: **all clicks now route through a single
  `safeClick(el)` helper** that verifies the element at runtime and refuses anything that isn't the
  local blob-download anchor or a Fidelity expander/group toggle (a link is never clicked). The
  static safety scan now also bans selecting anchors/links and requires the `safeClick` guard.
- The exporter **never blocks a working export**: it clicks "Expand groups" only when a group is
  genuinely collapsed (so already-visible positions are never toggled shut), and if a group stays
  collapsed it scrapes what is present and warns rather than aborting.

## [0.1.0] - 2026-07-02
### Added
- Read-only Fidelity per-lot browser exporter (`scripts/browser/fidelity_lot_export.js`) that runs
  in the user's own session, scrapes the AG-grid `posweb-purchase-history` lot tables, computes
  long/short holding term, and downloads a local `fidelity_lots.csv`. Makes zero network calls and
  clicks only Fidelity's own lot-expand buttons.
- Read-only DOM inspector (`scripts/browser/fidelity_dom_inspector.js`) for re-tuning selectors if
  Fidelity changes its Positions UI.
- Stdlib-only SQLite analyzer (`scripts/analyze/portfolio.py`) with `load`, `summary`, `symbol`,
  `accounts`, and a hardened read-only `query` subcommand. Recomputes holding term authoritatively
  (long > 1 year, short <= 1 year; Feb-29 clamps to Feb-28).
- Test suite (`tests/`): analyzer correctness incl. term boundaries, a static browser-safety scan,
  a data-safety scan, and release-notes checks.
- Copilot skill definition (`SKILL.md`) and project `README.md`.
- CI pipeline, release automation, and owner/Dependabot auto-merge.

[Unreleased]: https://github.com/BlancosWay/Fidelity-portfolio-lab/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/BlancosWay/Fidelity-portfolio-lab/releases/tag/v0.1.0
