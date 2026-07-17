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

  // ALL pinned-left rows in order -- reveals non-position rows we currently skip, especially the
  // per-account "Pending activity" line (unsettled trades) that should roll into cash-per-account,
  // plus account headers and cash/core rows. Dump each row's index, class list, sym-cell text, and
  // the center-grid Current value so we can see how a "Pending activity" row is structured/valued.
  const centerVal = ri => { const c = document.querySelector('.ag-center-cols-container [role="row"][row-index="' + ri + '"] [col-id="curVal"]'); return c ? clean(c.textContent) : ''; };
  const allRows = [...document.querySelectorAll('.ag-pinned-left-cols-container [role="row"]')]
    .map(r => ({ r, ri: parseInt(r.getAttribute('row-index'), 10) })).filter(x => !isNaN(x.ri)).sort((a, b) => a.ri - b.ri);
  P('\nALL PINNED ROWS (' + allRows.length + '):  [idx] classes | symText | curVal');
  allRows.forEach(({ r, ri }) => {
    const symCell = r.querySelector('[col-id="sym"]');
    P('  [' + ri + '] ' + tr(cls(r).replace(/\bag-row-\S+/g, '').replace(/\s+/g, ' ').trim(), 70)
      + ' | ' + JSON.stringify(tr(clean(symCell ? symCell.textContent : ''), 44))
      + ' | curVal=' + JSON.stringify(centerVal(ri)));
  });

  // Position symbol cells -- how each position renders its symbol/description in the pinned-left grid.
  // Purpose: some OPTION positions (esp. covered calls / cash-secured puts) export with the underlying
  // ticker + company name (e.g. "AAPL" / "APPLE INC") instead of the option contract name + expiry
  // (e.g. "GOOG 200 Put" / "Sep-18-2026"). Compare a known-good option row to a mislabeled one here to
  // see WHICH element holds the full option name so the exporter's symOf()/desc can be pointed at it.
  const symCells = [...document.querySelectorAll('.ag-pinned-left-cols-container [role="row"].posweb-row-position [col-id="sym"]')];
  P('\nPOSITION SYMBOL CELLS (' + symCells.length + '):  [row-index] span | desc | a11y | btnText | expandable');
  symCells.forEach(cell => {
    const row = cell.closest('[role="row"]');
    const nameBtn = cell.querySelector('.posweb-cell-symbol-name');
    const span = cell.querySelector('.posweb-cell-symbol-name_container > span');
    const desc = cell.querySelector('.posweb-cell-symbol-description');
    const a11y = [...cell.querySelectorAll('.posweb-cell-a11y_indicator')].map(e => clean(e.textContent)).filter(Boolean);
    P('  [' + (row ? row.getAttribute('row-index') : '?') + ']'
      + ' span=' + JSON.stringify(span ? clean(span.textContent) : null)
      + ' | desc=' + JSON.stringify(desc ? clean(desc.textContent) : null)
      + ' | a11y=' + JSON.stringify(a11y)
      + ' | btn=' + JSON.stringify(tr(clean(nameBtn ? nameBtn.textContent : ''), 40))
      + ' | expandable=' + !!(nameBtn && nameBtn.hasAttribute('aria-expanded')));
  });

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

  // Expanded drawer detail -- the authoritative option identity the user sees on expansion
  // (e.g. "GOOG Sep-18-2026 $200 PUT") lives in the drawer header, and the full purchase-history
  // table (every lot, every column) lives inside. Dump both, plus each row's cell count vs the header
  // count, so the exporter can be pointed at the real option name and the correct value columns.
  const drawer = document.querySelector('.posweb-drawer-detail');
  if (drawer) {
    P('\nEXPANDED DRAWER (.posweb-drawer-detail):');
    const heads = [...drawer.querySelectorAll('h1,h2,h3,h4,[class*="title"],[class*="header"],[class*="name"]')]
      .map(e => clean(e.innerText)).filter(Boolean);
    P('  headings/titles: ' + JSON.stringify([...new Set(heads)].slice(0, 12)));
    const ph = drawer.querySelector('table.posweb-purchase-history');
    if (ph) {
      const th = [...ph.querySelectorAll('thead th, thead td')].map(c => clean(c.innerText));
      P('  purchase-history THEAD (' + th.length + '): ' + th.join(' | '));
      const brs = [...ph.querySelectorAll('tbody tr')];
      P('  purchase-history rows: ' + brs.length + '  (Fidelity paginates ~10 lots/page -- a position with more is truncated unless we page through)');
      brs.forEach((r, i) => {
        const cellsArr = [...r.children].map(c => clean(c.innerText));
        P('   row' + i + ' (' + cellsArr.length + ' cells): ' + cellsArr.map(c => tr(c, 18)).join(' | '));
      });
    }
    // Pagination / "Show all" controls: dump EVERY interactive control in the drawer (tag, text,
    // href for anchors, aria-*, class) so the exporter can target the right one to reveal all lots
    // and add it to safeClick. We especially need to know if "Show all" / the page numbers are
    // <button> (safe) or <a href> (could navigate -- must be handled differently).
    const ctrls = [...drawer.querySelectorAll('*')].filter(e => e.tagName === 'BUTTON' || e.tagName === 'A' || e.getAttribute('role') === 'button');
    P('  drawer controls (' + ctrls.length + '):');
    ctrls.forEach(c => {
      P('    <' + c.tagName.toLowerCase() + '>'
        + ' text=' + JSON.stringify(tr(clean(c.innerText || c.textContent), 24))
        + (c.getAttribute('aria-label') ? ' aria-label=' + JSON.stringify(c.getAttribute('aria-label')) : '')
        + (c.tagName === 'A' ? ' href=' + JSON.stringify(c.getAttribute('href')) : '')
        + (c.getAttribute('role') ? ' role=' + JSON.stringify(c.getAttribute('role')) : '')
        + (c.getAttribute('aria-current') ? ' aria-current=' + JSON.stringify(c.getAttribute('aria-current')) : '')
        + (c.getAttribute('aria-controls') ? ' aria-controls=' + JSON.stringify(c.getAttribute('aria-controls')) : '')
        + ' class=' + JSON.stringify(tr(cls(c), 70)));
    });
  } else {
    P('\nEXPANDED DRAWER: none open - expand ONE position (+) first, then re-run.');
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
