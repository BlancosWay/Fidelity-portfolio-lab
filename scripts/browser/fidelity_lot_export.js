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
 * allow pasting. Paste this whole file. It auto-expands any collapsed account groups, then expands
 * each position one at a time (~1-2 min for large accounts), prints summaries, downloads
 * fidelity_lots.csv, then collapses the positions back.
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
  const total = document.querySelectorAll(EXP).length;
  if (!total && !document.querySelector('table.posweb-purchase-history')) { console.warn('No expandable positions found. Make sure your positions are listed (not collapsed under account headers), then retry.'); return; }
  console.log(`Expanding ${total} positions... (~1-2 min for large accounts; one lot drawer at a time)`);
  let done = 0, guard = 0;
  while (guard++ < total + 20) {
    const btns = [...document.querySelectorAll(EXP)];
    if (!btns.length) break;
    const before = btns.length, b = btns[0];
    try { b.scrollIntoView({ block: 'center' }); safeClick(b); } catch (e) {}
    await waitUntil(() => document.querySelectorAll(EXP).length < before, 1800);
    if (++done % 10 === 0) console.log(`  expanded ${done}/${total}...`);
  }
  // Some accounts open the position drawer on the "Research" tab, so the Purchase-history lot table
  // (table.posweb-purchase-history, rendered inside the "...tabpanel-lots..." panel) is absent.
  // Activate each drawer's "Purchase history" tab -- Fidelity's own in-drawer <button role="tab"> --
  // so the lots render before we scrape. Clicking it only switches the drawer's tab; it has no href
  // and never navigates (safeClick verifies this at runtime). Drawers already on Purchase history
  // (aria-selected="true") are left untouched, so this is a no-op on accounts that default to it.
  const LOTS_TAB = 'button.posweb-header-tab-button[aria-controls^="posweb-drawer-tabpanel-lots"]';
  const lotTabs = [...document.querySelectorAll(LOTS_TAB)].filter(b => b.getAttribute('aria-selected') !== 'true');
  if (lotTabs.length) {
    console.log(`Switching ${lotTabs.length} drawer(s) to the Purchase history tab...`);
    lotTabs.forEach(b => { try { safeClick(b); } catch (e) {} });
  }
  console.log('Waiting for lot data to finish loading...');
  await sleep(2800); // floor: let the first drawers' lot tables start rendering
  // Then wait until the lot-table count STOPS growing (stabilises) so large accounts finish their
  // lazy loads before we scrape; breaks after ~1.6s with no change, hard cap ~25s.
  let lastCount = -1, stableTicks = 0, waited = 0;
  while (waited < 25000) {
    const c = document.querySelectorAll('table.posweb-purchase-history').length;
    if (c === lastCount) { if (++stableTicks >= 4) break; } else { stableTicks = 0; lastCount = c; }
    await sleep(400); waited += 400;
  }

  const plRows = [...document.querySelectorAll('.ag-pinned-left-cols-container [role="row"]')];
  const positions = [], accounts = [];
  plRows.forEach(r => {
    const ri = parseInt(r.getAttribute('row-index'), 10); if (isNaN(ri)) return;
    const cell = r.querySelector('[col-id="sym"]'); if (!cell) return;
    if (r.classList.contains('posweb-row-account')) {
      const p = txt(cell, '.posweb-cell-account_primary'), s = txt(cell, '.posweb-cell-account_secondary');
      accounts.push({ ri, account: clean(p + ' ' + s) || clean(cell.textContent).replace(/^Account:\s*/i, '') });
    } else {
      const nameBtn = cell.querySelector('.posweb-cell-symbol-name');
      if (!nameBtn) return;
      const symbol = txt(nameBtn, '.posweb-cell-symbol-name_container > span') || clean(nameBtn.textContent).split(' ')[0];
      positions.push({ ri, symbol, desc: txt(cell, '.posweb-cell-symbol-description'), sub: txt(cell, '.posweb-cell-a11y_indicator') });
    }
  });
  positions.sort((x, y) => x.ri - y.ri); accounts.sort((x, y) => x.ri - y.ri);
  const ownerFor = D => {
    let sym = null; for (const p of positions) { if (p.ri <= D) sym = p; else break; }
    let acc = null; for (const a of accounts) { if (a.ri <= D) acc = a; else break; }
    return { sym, acc };
  };

  const H = heads => { const f = re => heads.findIndex(h => re.test(h.toLowerCase())); return { acq: f(/acquired/), term: f(/term/), qty: f(/quantity/), val: f(/current value/), avg: f(/average cost/), cost: f(/cost basis total/), gl: heads.findIndex(h => /gain\/loss/i.test(h) && h.includes('$')), glp: heads.findIndex(h => /gain\/loss/i.test(h) && h.includes('%')) }; };
  const lots = [];
  document.querySelectorAll('table.posweb-purchase-history').forEach(t => {
    const heads = [...t.querySelectorAll('thead th, thead td')].map(c => clean(c.innerText));
    const ix = H(heads);
    const dr = t.closest('[role="row"]');
    const D = dr ? parseInt(dr.getAttribute('row-index'), 10) : Infinity;
    const { sym, acc } = ownerFor(D);
    t.querySelectorAll('tbody tr').forEach(r => {
      const c = [...r.children].map(x => clean(x.innerText));
      const acq = ix.acq >= 0 ? c[ix.acq] : ''; if (!parseAcq(acq)) return;
      lots.push({ account: acc ? acc.account : '', symbol: sym ? sym.symbol : '(unknown)', description: sym ? sym.desc : '', type: sym ? sym.sub : '', quantity: ix.qty >= 0 ? c[ix.qty] : '', acquired: acq, termComputed: termOf(parseAcq(acq)), termFidelity: ix.term >= 0 ? c[ix.term] : '', avgCost: ix.avg >= 0 ? c[ix.avg] : '', costTotal: ix.cost >= 0 ? c[ix.cost] : '', value: ix.val >= 0 ? c[ix.val] : '', gl: ix.gl >= 0 ? c[ix.gl] : '', glp: ix.glp >= 0 ? c[ix.glp] : '' });
    });
  });

  document.querySelectorAll('.ag-pinned-left-cols-container button.posweb-cell-symbol-name[aria-expanded="true"]').forEach(b => { try { safeClick(b); } catch (e) {} });

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
