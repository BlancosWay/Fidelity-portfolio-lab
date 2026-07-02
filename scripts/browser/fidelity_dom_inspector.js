/*
 * Fidelity Positions -> READ-ONLY DOM inspector (maintenance tool).
 *
 * SAFE (audit me — enforced by tests/test_browser_safety.py):
 *   - Zero network calls; no credential/storage reads; NO page clicks at all.
 *   - Reads the DOM and downloads a local text report describing the current grid / lot-table /
 *     expander structure. Use this if Fidelity changes its Positions UI and the exporter stops
 *     finding lots, then update fidelity_lot_export.js selectors to match the report.
 *   - The only click is on a local download anchor (the report file).
 *
 * USE: On Fidelity "Positions", expand ONE position's lots (+) so a purchase-history table shows.
 * Console (Ctrl+Shift+J); if prompted type: allow pasting. Paste this whole file. It saves
 * fidelity_dom_report.txt.
 */
(() => {
  const OUT = []; const P = (...args) => { const s = args.join(' '); OUT.push(s); console.log(s); };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const tr = (s, n) => { s = s || ''; return s.length > n ? s.slice(0, n) + '...' : s; };
  const cls = e => (e && e.getAttribute && e.getAttribute('class')) || '';

  P('=== FIDELITY DOM REPORT ===  ' + location.href);

  const grids = [...document.querySelectorAll('[role="grid"],[role="treegrid"],[role="table"]')];
  P('grids: ' + grids.length);
  grids.forEach((g, gi) => {
    P(`--- GRID ${gi} role=${g.getAttribute('role')} class="${tr(cls(g), 80)}"`);
    const heads = [...g.querySelectorAll('[role="columnheader"]')].map(c => clean(c.innerText)).filter(Boolean);
    P('  columnheaders(' + heads.length + '): ' + (heads.join(' | ') || '(none)'));
    P('  rows: ' + g.querySelectorAll('[role="row"]').length);
  });

  const expanders = document.querySelectorAll('.ag-pinned-left-cols-container button.posweb-cell-symbol-name[aria-expanded]');
  P('\nposweb expander buttons: ' + expanders.length + ' (export script expands these)');
  const accounts = document.querySelectorAll('.posweb-row-account');
  P('account rows (.posweb-row-account): ' + accounts.length);

  const lt = document.querySelector('table.posweb-purchase-history') || document.querySelector('table.pvd-table__table');
  if (lt) {
    P('\nLOT TABLE class="' + cls(lt) + '"');
    P('  THEAD: ' + [...lt.querySelectorAll('thead th, thead td')].map(c => clean(c.innerText)).join(' | '));
    const brs = [...lt.querySelectorAll('tbody tr')];
    P('  tbody rows: ' + brs.length);
    brs.slice(0, 2).forEach((r, i) => { P('   row' + i + ': ' + [...r.children].map(c => tr(clean(c.innerText), 22)).join(' | ')); });
    const dr = lt.closest('[role="row"]');
    P('  containing row-index: ' + (dr ? dr.getAttribute('row-index') : '(none)'));
  } else {
    P('\nLOT TABLE: none rendered - expand ONE position (+) first, then re-run.');
  }

  const vp = document.querySelector('.ag-body-viewport');
  if (vp) P('\nviewport scrollHeight=' + vp.scrollHeight + ' clientHeight=' + vp.clientHeight + ' (equal => not virtualized)');
  P('\n=== END REPORT ===');

  const blob = new Blob([OUT.join('\n')], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'fidelity_dom_report.txt';
  // safeClick -- the ONLY click in this read-only inspector: it clicks the local blob-download
  // anchor and refuses anything else (it never clicks a link or page control).
  const safeClick = el => {
    if (el && el.tagName === 'A' && el.download && String(el.href || '').startsWith('blob:')) { el.click(); return true; }
    return false;
  };
  document.body.appendChild(a); safeClick(a); a.remove();
  console.log('%cSaved fidelity_dom_report.txt', 'color:green;font-weight:bold');
})();
