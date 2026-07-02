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
