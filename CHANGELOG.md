# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- **`--max-ordinary-offset` on `harvest` and `dashboard`** (default `3000`) parameterizes the annual
  net-capital-loss deduction against ordinary income, so a married-filing-separately filer can set
  `1500`. `_net_capital_tax`, `harvest`, and `liquidation_estimate` thread the cap through; the default
  keeps every existing estimate unchanged. Estimates only, not tax advice.

### Fixed
- **`load` tolerates benign Fidelity header drift instead of bricking.** The exact header-equality check
  rejected the whole export if Fidelity added, renamed-adjacent, or reordered a single column. Values are
  now mapped by column NAME, so extra and reordered columns are ignored; only a genuinely MISSING
  required column raises (with a clear "missing"/"got" diff).
- **Wash-sale disallowed loss is now quantity-aware.** A small replacement purchase against a larger
  loss lot previously flagged the *entire* loss; the IRS only disallows the loss on the shares matched by
  the replacement. Each candidate now reports `affected_shares` and a quantity-apportioned
  `disallowed_loss` (`loss × matched_shares ÷ loss_shares`), and `washsale` shows a "Disallowed $ (est)"
  column so the remaining loss stays visibly allowed.
- **Wash-sale now surfaces non-BUY re-acquisitions (option assignment/exercise, inbound transfers).**
  A replacement position re-acquired via an option assignment/exercise or an inbound transfer/exchange/
  journal was classified `OTHER` and silently rated CLEAN. These are now treated as **inferred**
  acquisitions (counted only when shares actually came in, `signed_qty > 0`) and, because the inference
  is less certain than an explicit purchase, their status is **capped at REVIEW** — never BLOCKED/CAUTION
  — so the tool flags them for verification without asserting a definite wash sale. Definite BUY/REINVEST/
  buy-to-open keep the full severity map.
- **`wash_category` now uses a word boundary for "529", matching `is_taxable`.** A taxable account whose
  name merely contained the digits "529" (e.g. "Individual 5291", "X529 Brokerage") was classified as a
  529 plan for wash-sale severity — softening a genuine taxable wash sale from CAUTION to REVIEW — even
  though `is_taxable` (with its `\b529\b`) correctly treated it as taxable. The two classifiers now agree.
- **`sell` now nets short- vs long-term and caps the loss benefit like every other tax command.** Its
  estimated tax previously applied the ST and LT rates to each bucket independently, so a loss-heavy or
  mixed-character sale printed a wrong (sometimes ~7×-overstated or fake-negative) number that
  contradicted the netting used by `harvest`/`liquidation`/`dashboard`. `select_lots`/`sell` now route
  `est_tax` through `_net_capital_tax` (ST↔LT netting, `--max-ordinary-offset` cap) and surface the
  deductible-now vs carryforward split. Pure-gain sales are unchanged. Estimates only, not tax advice.
- **Closed lots (quantity ≤ 0) are no longer treated as live positions.** A zero-quantity (fully sold)
  or negative/short lot that still carried a `gain_loss` in the export was counted as a harvestable
  loss, as taxable in the "if sold now" liquidation estimate, in per-account unrealized gain/loss, in
  `ripening` ("harvest before it ripens"), and as a `gift` donation candidate. `harvest` (via
  `taxable_loss_candidates`), `liquidation_estimate`, `unrealized_by_account`, `ripening`, and `gift`
  now skip any lot whose quantity is not a strictly positive number (blank/non-numeric quantities are
  treated as not-live), so only open positions are analyzed.
- **`concentration` excludes options and non-positive-value symbols from the equity ranking.** An
  option lot's `current_value` is the premium, not the notional exposure, so ranking it as a single-name
  equity position overstated diversification risk; options are now dropped from the ranking (count noted,
  pointing to the `options` command). A symbol whose aggregated value is non-positive (a short position
  or a corrupt/negative scraped value) is also excluded so a single bad value can no longer make total
  invested `<= 0` and collapse the whole report to "no positions"; both exclusion counts are printed in
  the populated and the empty output.
- **`summary` and `symbol` now recompute the holding term as of a date and are read-only.** The DB
  stores the Long/Short term computed at `load` time, so after a lot crossed its one-year mark the
  reports still showed it as short-term. Both commands now recompute each lot's term from its
  acquisition date (new pure `holdings_overview` helper) and accept `--as-of YYYY-MM-DD` (default
  today); they also open the DB read-only via the same missing-portfolio guard as the other commands.
  Cash is still shown per symbol and counted in each account's market value — it is excluded only from
  the Long/Short term split, exactly as before.
- **Analysis commands are strictly read-only and fail gracefully on a missing portfolio.** Running any
  command against a never-loaded (or since-deleted) DB previously either created a 0-byte SQLite file
  or raised a raw `sqlite3` traceback. Every command now opens the DB read-only, and on a missing file
  or missing `lots` table prints a one-line hint (`No portfolio loaded at <db>. Run: ... load <lots.csv>`)
  and exits without creating anything. `query` returns a non-zero exit code in that case.
- **`sell` and `harvest` now warn on inconsistent per-share prices.** The browser export can carry
  different `current_value/quantity` across lots of the same symbol (a scrape corruption); the tax
  tools previously trusted these silently and could surface a phantom loss. A new detector flags any
  symbol whose per-share price disagrees across its lots, and `sell`/`harvest` print a warning to
  verify before acting (the numbers themselves are unchanged).
- **`options`/`expiration` no longer count already-expired contracts as live.** Options whose expiry
  is before the as-of date are excluded from `options` exposure (premium/notional/bias/coverage; the
  count of excluded lots is noted), and from `expiration`'s live/soon/assignment metrics and
  `nearest`-expiry (expired rows are still listed with a separate `expired` count so nothing is hidden).
- **`harvest` benefit and `dashboard` "If sold now" now model capital-loss netting + the $3,000 cap.**
  Previously each tax figure applied the ST/LT rate to that bucket's signed total independently, so a
  net capital loss could print a large negative "tax" (a fake refund) and the harvest benefit ignored
  that a net loss only offsets $3,000 of ordinary income per year. Both now net short-term against
  long-term first, cap a residual net loss at the $3,000 ordinary-income offset (labeling the rest a
  carryforward), and never report a negative tax on a net gain. `harvest` gains `--offsetting-st-gains`
  / `--offsetting-lt-gains` so harvested losses can be valued against known realized gains. Estimates
  only, not tax advice.
- **`sell` no longer operates on tax-advantaged accounts.** Lot selection (`hifo`/`fifo`/`loss-first`/
  `min-tax`) now excludes IRA/Roth/HSA/BrokerageLink/529 lots — their gains are tax-free, so the tool
  never recommends selling a retirement lot or charges phantom capital-gains tax on one. A pick that
  spans multiple accounts prints a per-account NOTE (specific-ID sales are one order per account).

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
