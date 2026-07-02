/*
 * Fidelity Positions -> per-lot CSV + long/short (holding-period) summary.
 *
 * READ-ONLY & COMPLIANT (audit me — enforced by tests/test_browser_safety.py):
 *   - Zero network calls (no fetch / XHR / WebSocket / sendBeacon / EventSource / Image beacons).
 *   - No credential/storage reads (no document.cookie / localStorage / sessionStorage).
 *   - Runs in YOUR already-authenticated session; only reads the DOM and downloads a local CSV.
 *   - The ONLY elements it clicks are Fidelity's own lot-expand buttons
 *     (button.posweb-cell-symbol-name[aria-expanded]), the read-only "Expand groups" button, the
 *     in-drawer "Purchase history" tab button, and a local download anchor. It never clicks
 *     trade/links and never navigates.
 *   - "long" = held > 1 year, "short" = held <= 1 year, from each lot's Acquired date (Feb-29
 *     acquisitions clamp to Feb-28 so Mar-1 is their first long-term day). Verified against
 *     Fidelity's own Term column on 277 real lots.
 *
 * USE: On Fidelity "Positions" (All accounts is fine). Console (Ctrl+Shift+J); if prompted type:
 * allow pasting. Paste this whole file. It auto-expands any collapsed account groups, then reads
 * each position's lots ONE AT A TIME (expand -> read -> collapse, so the grid never grows large
 * enough for Fidelity to drop off-screen rows), prints summaries, and downloads fidelity_lots.csv.
 * A large account (100+ positions) can take several minutes -- watch the console progress.
 */
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const num = s => { const n = parseFloat(String(s).replace(/[()]/g, '-').replace(/[^0-9.\-]/g, '')); return isNaN(n) ? 0 : n; };
  const AS_OF = new Date(); AS_OF.setHours(0, 0, 0, 0); // compare calendar dates, not instants
  const MON = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };
  const parseAcq = s => { s = clean(s); const m = s.match(/([A-Za-z]{3})[-\s](\d{1,2})[-,\s]+(\d{4})/); if (m && MON[m[1].toLowerCase()] != null) return new Date(+m[3], MON[m[1].toLowerCase()], +m[2]); const d = new Date(s); return isNaN(+d) ? null : d; };
  const termOf = acq => {
    if (!acq) return '';
    const a = new Date(acq);
    const leapDay = a.getMonth() === 1 && a.getDate() === 29;
    a.setFullYear(a.getFullYear() + 1);
    if (leapDay && a.getMonth() === 2) a.setMonth(1, 28); // Feb-29 acquisition -> Feb-28 anniversary
    return AS_OF > a ? 'Long-Term' : 'Short-Term';
  };
  const txt = (el, sel) => { const n = el && el.querySelector(sel); return n ? clean(n.textContent) : ''; };
  const EXP = '.ag-pinned-left-cols-container button.posweb-cell-symbol-name[aria-expanded="false"]';
  const waitUntil = async (fn, t) => { const t0 = Date.now(); while (Date.now() - t0 < t) { if (fn()) return true; await sleep(80); } return false; };

  // safeClick -- the ONLY place a click happens. It verifies the element at RUNTIME and refuses
  // anything that is not one of our four approved read-only targets: the local blob-download anchor,
  // a Fidelity position lot-expander button, an account-group expand toggle, or the in-drawer
  // "Purchase history" tab button. A link or any other element is never clicked -- defence in depth
  // for the read-only / no-navigation model.
  const safeClick = el => {
    if (!el || !el.tagName) return false;
    // The ONLY anchor we ever click is our own local blob-download link; every other link is refused.
    if (el.tagName === 'A') {
      if (el.download && String(el.href || '').startsWith('blob:')) { el.click(); return true; }
      return false;
    }
    // Refuse any element nested inside a link (a bubbled click could navigate).
    for (let p = el.parentElement; p; p = p.parentElement) { if (p.tagName === 'A') return false; }
    const cls = String(el.className || '');
    const okExpander = el.tagName === 'BUTTON' && /posweb-cell-symbol-name/.test(cls);
    const okGroup = /group-contracted/.test(cls) || (el.tagName === 'BUTTON' && /^\s*expand\s+groups?\s*$/i.test(el.textContent || ''));
    // The in-drawer "Purchase history" tab: a <button role="tab"> that controls a "...tabpanel-lots..."
    // panel. Clicking it only switches the drawer's tab -- it has no href and never navigates.
    const okLotsTab = el.tagName === 'BUTTON' && /posweb-header-tab-button/.test(cls) && el.getAttribute('role') === 'tab' && /^posweb-drawer-tabpanel-lots/.test(el.getAttribute('aria-controls') || '');
    if (okExpander || okGroup || okLotsTab) { el.click(); return true; }
    return false;
  };

  // Phase 0 -- auto-expand any collapsed account groups so every position is in the DOM before we
  // scrape. On the "All accounts" view, positions live under collapsible "Account:" rows. A collapsed
  // account is detected STRUCTURALLY -- an "Account:" row (.posweb-row-account) with no position row
  // (.posweb-row-position) immediately after it -- because Fidelity leaves the ag-row-group-contracted
  // class on rows even when they are expanded, so that class must NOT be used to decide. When
  // something is collapsed we click Fidelity's own read-only "Expand groups" button (which expands
  // all). We never click when positions are already visible (so nothing gets toggled shut) and we
  // never abort: if a group stays collapsed we scrape what is present and warn, so a working export is
  // never blocked.
  const plRowsOrdered = () => [...document.querySelectorAll('.ag-pinned-left-cols-container [role="row"]')]
    .map(r => ({ r, ri: parseInt(r.getAttribute('row-index'), 10) }))
    .filter(x => !isNaN(x.ri)).sort((a, b) => a.ri - b.ri);
  const positionRows = () => document.querySelectorAll('.ag-pinned-left-cols-container [role="row"].posweb-row-position');
  const collapsedGroups = () => {
    const rows = plRowsOrdered(), out = [];
    for (let k = 0; k < rows.length; k++) {
      if (!rows[k].r.classList.contains('posweb-row-account')) continue;
      const next = rows[k + 1] && rows[k + 1].r;
      if (!next || next.classList.contains('posweb-row-account')) out.push(rows[k].r); // header with no rows under it
    }
    return out;
  };
  if (collapsedGroups().length) {
    console.log(`Expanding ${collapsedGroups().length} collapsed account group(s)...`);
    const g = [...document.querySelectorAll('button')].find(x => /^\s*expand\s+groups?\s*$/i.test(clean(x.textContent)));
    if (g) { try { safeClick(g); } catch (e) {} await waitUntil(() => !collapsedGroups().length && positionRows().length > 0, 6000); }
    if (collapsedGroups().length) {
      console.warn(`${collapsedGroups().length} account group(s) still collapsed; their positions may be omitted. Click "Expand groups" (or each "Account:" row) and re-run for a complete export.`);
    }
  }
  // The Positions grid is NOT scroll-virtualised (all rows stay in the DOM), so enumerate every
  // expandable position up front: its account (nearest preceding "Account:" row), symbol, and
  // margin/cash sub-label. We then process ONE position at a time -- expand it, read its lot table,
  // collapse it -- re-locating each position's expander button by (account, symbol) at click time
  // (never trusting a stored, possibly-stale element ref). Opening ALL drawers at once makes the grid
  // tall enough that Fidelity starts virtualising (dropping) off-screen drawers, which truncated large
  // accounts mid-way (missing lots); keeping at most one drawer open avoids that and captures all.
  const pinnedRowsNow = () => [...document.querySelectorAll('.ag-pinned-left-cols-container [role="row"]')]
    .map(r => ({ r, ri: parseInt(r.getAttribute('row-index'), 10) }))
    .filter(x => !isNaN(x.ri)).sort((a, b) => a.ri - b.ri);
  const acctOf = cell => cell ? (clean(txt(cell, '.posweb-cell-account_primary') + ' ' + txt(cell, '.posweb-cell-account_secondary')) || clean(cell.textContent).replace(/^Account:\s*/i, '')) : '';
  const symOf = nameBtn => txt(nameBtn, '.posweb-cell-symbol-name_container > span') || clean(nameBtn.textContent).split(' ')[0];
  const isExpander = nameBtn => !!nameBtn && nameBtn.tagName === 'BUTTON' && nameBtn.hasAttribute('aria-expanded');

  // expanders(): the live expander buttons in DOM order. We address each position by its ORDINAL in
  // this list -- collision-proof even if the same symbol appears twice in one account (e.g. a margin
  // and a cash row), and re-located fresh at click time so a row re-render can't invalidate a ref.
  // The grid is not virtualised and we keep at most one drawer open, so the list stays stable/ordered.
  const expanders = () => {
    const out = [];
    for (const { r } of pinnedRowsNow()) {
      if (!r.classList.contains('posweb-row-position')) continue;
      const cell = r.querySelector('[col-id="sym"]'); if (!cell) continue;
      const nameBtn = cell.querySelector('.posweb-cell-symbol-name');
      if (isExpander(nameBtn)) out.push(nameBtn);
    }
    return out;
  };
  const queue = [];
  let curAcct = '';
  pinnedRowsNow().forEach(({ r }) => {
    if (r.classList.contains('posweb-row-account')) { curAcct = acctOf(r.querySelector('[col-id="sym"]')) || curAcct; return; }
    if (!r.classList.contains('posweb-row-position')) return;
    const cell = r.querySelector('[col-id="sym"]'); if (!cell) return;
    const nameBtn = cell.querySelector('.posweb-cell-symbol-name');
    if (!isExpander(nameBtn)) return; // cash / non-expandable
    queue.push({ account: curAcct, symbol: symOf(nameBtn), desc: txt(cell, '.posweb-cell-symbol-description'), sub: txt(cell, '.posweb-cell-a11y_indicator') });
  });
  const expanderCount = document.querySelectorAll(EXP).length;
  if (!queue.length && !document.querySelector('table.posweb-purchase-history')) { console.warn('No expandable positions found. Make sure your positions are listed (not collapsed under account headers), then retry.'); return; }
  console.log(`Reading ${queue.length} positions (${expanderCount} collapsed expanders) ONE at a time -- keeps the grid small so nothing is dropped; a large account can take several minutes...`);

  const H = heads => { const f = re => heads.findIndex(h => re.test(h.toLowerCase())); return { acq: f(/acquired/), term: f(/term/), qty: f(/quantity/), val: f(/current value/), avg: f(/average cost/), cost: f(/cost basis total/), gl: heads.findIndex(h => /gain\/loss/i.test(h) && h.includes('$')), glp: heads.findIndex(h => /gain\/loss/i.test(h) && h.includes('%')) }; };
  const phTables = () => [...document.querySelectorAll('table.posweb-purchase-history')];
  // The in-drawer "Purchase history" tab button (some drawers open on "Research"; clicking it only
  // switches that drawer's own tab -- no href, never navigates; re-verified at runtime by safeClick).
  const LOTS_TAB = 'button.posweb-header-tab-button[aria-controls^="posweb-drawer-tabpanel-lots"]';
  const drawers = () => [...document.querySelectorAll('.posweb-drawer-detail')];
  const rowsIn = d => [...d.querySelectorAll('table.posweb-purchase-history')].reduce((n, t) => n + t.querySelectorAll('tbody tr').length, 0);
  const collapseOpen = () => document.querySelectorAll('.ag-pinned-left-cols-container button.posweb-cell-symbol-name[aria-expanded="true"]').forEach(b => { try { safeClick(b); } catch (e) {} });

  const lots = [];
  let done = 0, missed = 0;
  for (let i = 0; i < queue.length; i++) {
    const pos = queue[i];
    // Start from a clean state: collapse any drawer left open (previous position or a manual expand)
    // and wait until none remain, so the drawer we open next is unambiguously THIS position's.
    collapseOpen();
    await waitUntil(() => phTables().length === 0 && drawers().length === 0, 4000);
    const before = new Set(drawers()); // any drawer that stubbornly stayed open -- excluded below
    const btn = expanders()[i];
    if (!btn) { missed++; continue; }
    try { btn.scrollIntoView({ block: 'center' }); } catch (e) {}
    if (btn.getAttribute('aria-expanded') !== 'true') { try { safeClick(btn); } catch (e) {} }
    await waitUntil(() => btn.getAttribute('aria-expanded') === 'true', 2000);
    // THIS position's drawer is the one that just APPEARED (identified by DOM node, not description),
    // so a lingering stale drawer or a duplicate description can never be scraped in its place.
    await waitUntil(() => drawers().some(d => !before.has(d)), 3000);
    const drawer = drawers().find(d => !before.has(d)) || null;
    if (!drawer) { missed++; collapseOpen(); continue; } // fail closed: never scrape an unidentified drawer
    // if this drawer opened on the Research tab, switch it to Purchase history so the lots render
    const tab = drawer.querySelector(LOTS_TAB);
    if (tab && tab.getAttribute('aria-selected') !== 'true') { try { safeClick(tab); } catch (e) {} }
    const gotRows = await waitUntil(() => rowsIn(drawer) > 0, 5000);
    drawer.querySelectorAll('table.posweb-purchase-history').forEach(t => {
      const ix = H([...t.querySelectorAll('thead th, thead td')].map(c => clean(c.innerText)));
      t.querySelectorAll('tbody tr').forEach(r => {
        const c = [...r.children].map(x => clean(x.innerText));
        const acq = ix.acq >= 0 ? c[ix.acq] : ''; if (!parseAcq(acq)) return;
        lots.push({ account: pos.account, symbol: pos.symbol, description: pos.desc, type: pos.sub, quantity: ix.qty >= 0 ? c[ix.qty] : '', acquired: acq, termComputed: termOf(parseAcq(acq)), termFidelity: ix.term >= 0 ? c[ix.term] : '', avgCost: ix.avg >= 0 ? c[ix.avg] : '', costTotal: ix.cost >= 0 ? c[ix.cost] : '', value: ix.val >= 0 ? c[ix.val] : '', gl: ix.gl >= 0 ? c[ix.gl] : '', glp: ix.glp >= 0 ? c[ix.glp] : '' });
      });
    });
    if (!gotRows) missed++;
    const cb = expanders()[i]; // collapse this drawer before the next one
    if (cb && cb.getAttribute('aria-expanded') === 'true') { try { safeClick(cb); } catch (e) {} }
    if (++done % 10 === 0) console.log(`  read ${done}/${queue.length} positions, ${lots.length} lots so far...`);
  }
  collapseOpen(); // safety net: collapse anything still open
  if (missed) console.warn(`${missed} position(s) returned no purchase-history rows; if a symbol looks short, re-run.`);

  if (!lots.length) { console.warn('Parsed 0 lots. Ensure positions are listed and re-run; if still empty, use fidelity_dom_inspector.js.'); return; }

  const bySym = {}, byAcct = {};
  lots.forEach(l => {
    const s = l.symbol, t = l.termComputed, q = num(l.quantity);
    (bySym[s] = bySym[s] || { units: 0, lots: 0, longUnits: 0, shortUnits: 0, accounts: new Set() });
    bySym[s].units += q; bySym[s].lots++; bySym[s][t === 'Long-Term' ? 'longUnits' : 'shortUnits'] += q; bySym[s].accounts.add(l.account);
    const a = l.account || '(unknown)'; (byAcct[a] = byAcct[a] || { longLots: 0, shortLots: 0 }); byAcct[a][t === 'Long-Term' ? 'longLots' : 'shortLots']++;
  });
  const symTable = {}; Object.keys(bySym).sort().forEach(s => { const x = bySym[s]; symTable[s] = { units: +x.units.toFixed(4), lots: x.lots, long_units: +x.longUnits.toFixed(4), short_units: +x.shortUnits.toFixed(4), accounts: x.accounts.size }; });
  const longLots = lots.filter(l => l.termComputed === 'Long-Term').length;
  console.log(`%cParsed ${lots.length} lots across ${Object.keys(bySym).length} symbols / ${Object.keys(byAcct).length} accounts  (long=${longLots}, short=${lots.length - longLots})`, 'color:green;font-weight:bold');
  console.log('Units per symbol (long = >1yr, short = <=1yr):'); console.table(symTable);
  console.log('Lot counts per account by term:'); console.table(byAcct);

  const cols = ['Account', 'Symbol', 'Description', 'Margin/Cash', 'Quantity', 'Date Acquired', 'Term (>1yr rule)', 'Term (Fidelity)', 'Average Cost Basis', 'Cost Basis Total', 'Current Value', 'Gain/Loss $', 'Gain/Loss %'];
  const esc = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
  const rows = lots.map(l => [l.account, l.symbol, l.description, l.type, l.quantity, l.acquired, l.termComputed, l.termFidelity, l.avgCost, l.costTotal, l.value, l.gl, l.glp].map(esc).join(','));
  const csv = [cols.join(','), ...rows].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'fidelity_lots.csv';
  document.body.appendChild(a); safeClick(a); a.remove();
  console.log('%cSaved fidelity_lots.csv', 'color:green;font-weight:bold');
})();
