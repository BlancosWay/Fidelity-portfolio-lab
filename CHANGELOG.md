# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
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
