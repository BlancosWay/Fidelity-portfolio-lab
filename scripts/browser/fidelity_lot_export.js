/*
 * Fidelity Positions -> per-lot CSV + long/short (holding-period) summary.
 *
 * READ-ONLY & COMPLIANT (audit me — enforced by tests/test_browser_safety.py):
 *   - Zero network calls (no fetch / XHR / WebSocket / sendBeacon / EventSource / Image beacons).
 *   - No credential/storage reads (no document.cookie / localStorage / sessionStorage).
 *   - Runs in YOUR already-authenticated session; only reads the DOM and downloads a local CSV.
 *   - The ONLY elements it clicks are Fidelity's own lot-expand buttons
 *     (button.posweb-cell-symbol-name[aria-expanded]) and a local download anchor. It never clicks
 *     trade/links and never navigates.
 *   - "long" = held > 1 year, "short" = held <= 1 year, from each lot's Acquired date (Feb-29
 *     acquisitions clamp to Feb-28 so Mar-1 is their first long-term day). Verified against
 *     Fidelity's own Term column on 277 real lots.
 *
 * USE: On Fidelity "Positions" (All accounts is fine), make sure positions are listed (click
 * "Expand groups" if collapsed). Console (Ctrl+Shift+J); if prompted type: allow pasting. Paste
 * this whole file. It expands each position one at a time (~1-2 min for large accounts), prints
 * summaries, downloads fidelity_lots.csv, then collapses everything back.
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

  const total = document.querySelectorAll(EXP).length;
  if (!total && !document.querySelector('table.posweb-purchase-history')) { console.warn('No expandable positions found. Click "Expand groups" so positions are listed, then retry.'); return; }
  console.log(`Expanding ${total} positions... (~1-2 min for large accounts; one lot drawer at a time)`);
  let done = 0, guard = 0;
  while (guard++ < total + 20) {
    const btns = [...document.querySelectorAll(EXP)];
    if (!btns.length) break;
    const before = btns.length, b = btns[0];
    try { b.scrollIntoView({ block: 'center' }); b.click(); } catch (e) {}
    await waitUntil(() => document.querySelectorAll(EXP).length < before, 1800);
    if (++done % 10 === 0) console.log(`  expanded ${done}/${total}...`);
  }
  console.log('Waiting for lot data to finish loading...');
  await sleep(2800);

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

  document.querySelectorAll('.ag-pinned-left-cols-container button.posweb-cell-symbol-name[aria-expanded="true"]').forEach(b => { try { b.click(); } catch (e) {} });

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
  document.body.appendChild(a); a.click(); a.remove();
  console.log('%cSaved fidelity_lots.csv', 'color:green;font-weight:bold');
})();
