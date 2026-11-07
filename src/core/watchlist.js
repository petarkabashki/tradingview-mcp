/**
 * Core watchlist logic.
 * Reads via DOM rows (panel auto-opened when needed). Removal uses
 * TradingView's symbols_list REST API from the page context (cookie auth),
 * mirroring the proven alerts REST pattern. Add drives the Add-symbol
 * search UI so bare tickers resolve the same way they do for a human.
 */
import { evaluate, evaluateAsync, getClient, safeString } from '../connection.js';

// TV renamed the right-rail button: current builds use data-name="base" with
// aria-label "Watchlist, details, and news"; older builds used
// data-name="base-watchlist-widget-button" / aria-label "Watchlist".
const WL_BUTTON_JS = `(document.querySelector('[data-name="base-watchlist-widget-button"]')
  || document.querySelector('[aria-label="Watchlist, details, and news"]')
  || document.querySelector('[aria-label^="Watchlist"]'))`;

// The watchlist widget lazy-loads after the panel opens; a fixed 500ms wait
// raced it (issue #164). Poll until its Add-symbol button or rows exist.
async function ensureWatchlistOpen(maxWaitMs = 5000) {
  const state = await evaluate(`
    (function() {
      var btn = ${WL_BUTTON_JS};
      if (!btn) return { error: 'Watchlist button not found' };
      var pressed = btn.getAttribute('aria-pressed') === 'true';
      var widgetReady = !!(document.querySelector('[data-name="add-symbol-button"]')
        || document.querySelector('[class*="layout__area--right"] [data-symbol-full]'));
      if (!pressed || !widgetReady) { if (!pressed) btn.click(); return { opened: !pressed }; }
      return { opened: false, ready: true };
    })()
  `);
  if (state?.error) throw new Error(state.error);
  if (state?.ready) return { opened: false };

  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const ready = await evaluate(`
      !!(document.querySelector('[data-name="add-symbol-button"]')
        || document.querySelector('[class*="layout__area--right"] [data-symbol-full]'))
    `);
    if (ready) return { opened: !!state?.opened };
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error('Watchlist panel did not become ready. Is a watchlist widget configured in the right panel?');
}

// Active watchlist metadata (id, name, symbols) read from the React fiber
// tree — needed for the REST endpoints. Approach from PR #65.
async function getActiveListInfo() {
  return evaluate(`
    (function() {
      var panel = document.querySelector('[class*="layout__area--right"]');
      if (!panel) return null;
      var rows = panel.querySelectorAll('[data-symbol-full]');
      if (!rows.length) return null;
      var row = rows[0];
      var reactKey = Object.keys(row).find(function(k) { return k.indexOf('__reactFiber') === 0; });
      if (!reactKey) return null;
      var fiber = row[reactKey];
      var count = 0;
      while (fiber && count < 45) {
        if (fiber.memoizedProps && fiber.memoizedProps.current && fiber.memoizedProps.current.id) {
          var cur = fiber.memoizedProps.current;
          return { id: cur.id, name: cur.name, symbols: cur.symbols || [] };
        }
        fiber = fiber.return;
        count++;
      }
      return null;
    })()
  `);
}

export async function get() {
  await ensureWatchlistOpen();

  // Positional cell mapping (name, last, change, change%, volume) with
  // Unicode-minus normalization. The old regex classifier dropped every
  // negative value (TV renders U+2212, not ASCII '-') and all tick-notation
  // prices like 106'28'7 — issue #111.
  const data = await evaluate(`
    (function() {
      function norm(t) { return t.replace(/\\u2212/g, '-').trim(); }
      var container = document.querySelector('[class*="layout__area--right"]');
      if (!container) return { symbols: [], source: 'no_container' };
      var results = [];
      var seen = {};
      var symbolEls = container.querySelectorAll('[data-symbol-full]');
      for (var i = 0; i < symbolEls.length; i++) {
        var sym = symbolEls[i].getAttribute('data-symbol-full');
        if (!sym || seen[sym]) continue;
        seen[sym] = true;
        var row = symbolEls[i].closest('[class*="row"]') || symbolEls[i].parentElement;
        var cells = row ? row.querySelectorAll('[class*="cell"], [class*="column"]') : [];
        var texts = [];
        for (var j = 0; j < cells.length; j++) texts.push(norm(cells[j].textContent));
        results.push({
          symbol: sym,
          last: texts[1] || null,
          change: texts[2] || null,
          change_percent: texts[3] || null,
          volume: texts[4] || null,
        });
      }
      return { symbols: results, source: results.length ? 'dom_rows' : 'empty' };
    })()
  `);

  const listInfo = await getActiveListInfo();
  return {
    success: true,
    count: data?.symbols?.length || 0,
    source: data?.source || 'unknown',
    ...(listInfo && { list_id: listInfo.id, list_name: listInfo.name }),
    symbols: data?.symbols || [],
  };
}

export async function add({ symbol }) {
  const c = await getClient();
  await ensureWatchlistOpen();

  const addClicked = await evaluate(`
    (function() {
      var btn = document.querySelector('[data-name="add-symbol-button"]')
        || document.querySelector('[aria-label="Add symbol"]')
        || document.querySelector('[aria-label*="Add symbol"]');
      if (!btn || btn.offsetParent === null) return { found: false };
      btn.click();
      return { found: true };
    })()
  `);
  if (!addClicked?.found) throw new Error('Add symbol button not found in watchlist panel');
  await new Promise(r => setTimeout(r, 400));

  await c.Input.insertText({ text: symbol });
  await new Promise(r => setTimeout(r, 700));
  await c.Input.dispatchKeyEvent({ type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  await c.Input.dispatchKeyEvent({ type: 'keyUp', key: 'Enter', code: 'Enter' });
  await new Promise(r => setTimeout(r, 400));
  await c.Input.dispatchKeyEvent({ type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await c.Input.dispatchKeyEvent({ type: 'keyUp', key: 'Escape', code: 'Escape' });
  await new Promise(r => setTimeout(r, 400));

  // Verify the row actually appeared instead of reporting blind success.
  const bare = symbol.split(':').pop().toUpperCase();
  const verified = await evaluate(`
    (function() {
      var rows = document.querySelectorAll('[class*="layout__area--right"] [data-symbol-full]');
      for (var i = 0; i < rows.length; i++) {
        var s = rows[i].getAttribute('data-symbol-full') || '';
        if (s.toUpperCase() === ${JSON.stringify(symbol.toUpperCase())} || s.split(':').pop().toUpperCase() === ${JSON.stringify(bare)}) return s;
      }
      return null;
    })()
  `);

  return { success: !!verified, symbol, added_as: verified, action: verified ? 'added' : 'not_verified' };
}

export async function addBulk({ symbols }) {
  const results = [];
  for (const symbol of symbols) {
    try {
      const r = await add({ symbol });
      results.push({ symbol, success: r.success, added_as: r.added_as });
    } catch (err) {
      results.push({ symbol, success: false, error: err.message });
    }
  }
  const added = results.filter(r => r.success).length;
  return { success: added > 0, added, failed: results.length - added, results };
}

export async function remove({ symbols }) {
  await ensureWatchlistOpen();
  const listInfo = await getActiveListInfo();
  if (!listInfo) throw new Error('Cannot read active watchlist metadata (React fiber probe failed)');

  // Match requested symbols (bare or EXCHANGE:SYMBOL) against the list.
  const toRemove = [];
  const skipped = [];
  for (const sym of symbols) {
    if (sym.includes(':')) {
      if (listInfo.symbols.includes(sym)) toRemove.push(sym);
      else skipped.push(sym);
    } else {
      const match = listInfo.symbols.find(s => s.split(':').pop().toUpperCase() === sym.toUpperCase());
      if (match) toRemove.push(match);
      else skipped.push(sym);
    }
  }
  if (!toRemove.length) {
    return { success: false, removed: [], skipped, error: 'No matching symbols in the active watchlist' };
  }

  // Page-context fetch — browser attaches session cookies automatically.
  const resp = await evaluateAsync(`
    fetch('https://www.tradingview.com/api/v1/symbols_list/custom/' + ${JSON.stringify(listInfo.id)} + '/remove/', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(${JSON.stringify(toRemove)}),
    })
      .then(function(r) { return r.text().then(function(t) { return { status: r.status, ok: r.ok, body: t.substring(0, 300) }; }); })
      .catch(function(e) { return { status: 0, ok: false, body: String(e) }; })
  `);

  if (!resp?.ok) {
    throw new Error(`Watchlist remove REST call failed (HTTP ${resp?.status}): ${resp?.body}`);
  }

  // The desktop widget doesn't live-sync API removals — remount it by
  // toggling the panel, then verify the rows are actually gone.
  await evaluate(`(function() { var btn = ${WL_BUTTON_JS}; if (btn) btn.click(); })()`);
  await new Promise(r => setTimeout(r, 400));
  await evaluate(`(function() { var btn = ${WL_BUTTON_JS}; if (btn) btn.click(); })()`);

  let stillPresent = toRemove;
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 500));
    stillPresent = await evaluate(`
      (function() {
        var rows = document.querySelectorAll('[class*="layout__area--right"] [data-symbol-full]');
        var present = {};
        for (var i = 0; i < rows.length; i++) present[rows[i].getAttribute('data-symbol-full')] = true;
        return ${JSON.stringify(toRemove)}.filter(function(s) { return present[s]; });
      })()
    `) || [];
    if (stillPresent.length === 0) break;
  }

  return {
    success: true, removed: toRemove, skipped,
    verified: stillPresent.length === 0,
    list_id: listInfo.id, list_name: listInfo.name, api: 'rest',
  };
}

const SYMBOLS_LIST_API = 'https://www.tradingview.com/api/v1/symbols_list';

// Page-context REST call: the browser attaches the session cookies, so paths
// are fixed by each caller and no credentials ever leave the page.
async function symbolsListRequest(path, { method = 'GET', body } = {}) {
  const url = safeString(`${SYMBOLS_LIST_API}/${path}`);
  const headers = body === undefined
    ? `{ 'X-Requested-With': 'XMLHttpRequest' }`
    : `{ 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }`;
  const bodyPart = body === undefined ? '' : `, body: ${safeString(JSON.stringify(body))}`;
  const resp = await evaluateAsync(`
    fetch(${url}, { method: ${safeString(method)}, credentials: 'include', headers: ${headers}${bodyPart} })
      .then(function(r) { return r.text().then(function(t) { return { status: r.status, ok: r.ok, body: t }; }); })
      .catch(function(e) { return { status: 0, ok: false, body: String(e) }; })
  `);
  if (!resp?.ok) {
    throw new Error(`symbols_list ${method} ${path} failed (HTTP ${resp?.status}): ${(resp?.body || '').slice(0, 300)}`);
  }
  try { return JSON.parse(resp.body); }
  catch { return null; }
}

/** All custom watchlists on the account, with their symbols. */
export async function listAll() {
  const lists = await symbolsListRequest('custom/');
  const items = (Array.isArray(lists) ? lists : []).map(l => ({
    id: l.id,
    name: l.name,
    count: (l.symbols || []).length,
    symbols: l.symbols || [],
  }));
  return { success: true, count: items.length, lists: items };
}

/** Create a custom watchlist. Refuses a duplicate name rather than adding to it. */
export async function create({ name, symbols = [] }) {
  if (!name || !name.trim()) throw new Error('Watchlist name required');
  const { lists } = await listAll();
  if (lists.some(l => l.name === name)) {
    throw new Error(`Watchlist "${name}" already exists; it was left unchanged.`);
  }
  await symbolsListRequest('custom/', { method: 'POST', body: { name, symbols } });

  const made = (await listAll()).lists.find(l => l.name === name);
  if (!made) throw new Error(`Watchlist "${name}" was not found after creating it.`);
  const missing = symbols.filter(s => !made.symbols.includes(s));
  return { success: missing.length === 0, action: 'created', id: made.id, name, symbols: made.symbols, missing };
}

/**
 * Make a list we own hold exactly `symbols`. The id and name must both match,
 * so a recycled name can never rewrite a list the caller doesn't own.
 */
export async function sync({ id, name, symbols }) {
  if (!Number.isSafeInteger(id)) throw new Error(`Watchlist id must be an integer, got: ${id}`);
  const list = (await listAll()).lists.find(l => l.id === id);
  if (!list) throw new Error(`Watchlist ${id} no longer exists.`);
  if (list.name !== name) throw new Error(`Watchlist ${id} is named "${list.name}", expected "${name}".`);

  const toAdd = symbols.filter(s => !list.symbols.includes(s));
  const toRemove = list.symbols.filter(s => !symbols.includes(s));
  if (toAdd.length) await symbolsListRequest(`custom/${id}/append/`, { method: 'POST', body: toAdd });
  if (toRemove.length) await symbolsListRequest(`custom/${id}/remove/`, { method: 'POST', body: toRemove });

  const after = (await listAll()).lists.find(l => l.id === id);
  const missing = symbols.filter(s => !after?.symbols.includes(s));
  const extra = (after?.symbols || []).filter(s => !symbols.includes(s));
  return {
    success: missing.length === 0 && extra.length === 0,
    action: toAdd.length || toRemove.length ? 'updated' : 'unchanged',
    id, name, added: toAdd, removed: toRemove, missing, extra,
  };
}

/** Make `name` the active list in the attached tab, through the list picker. */
export async function switchList({ name }) {
  await ensureWatchlistOpen();
  const before = await getActiveListInfo();
  if (before?.name === name) return { success: true, action: 'already_active', name, list_id: before.id };

  const opened = await evaluate(`
    (function() {
      var menu = document.querySelector('[data-name="watchlists-button"]');
      if (!menu) return false;
      menu.click();
      return true;
    })()
  `);
  if (!opened) throw new Error('Watchlist menu button not found');
  await new Promise(r => setTimeout(r, 400));

  const dialog = await evaluate(`
    (function() {
      var item = document.querySelector('[aria-label^="Open list"]');
      if (!item) return false;
      item.click();
      return true;
    })()
  `);
  if (!dialog) throw new Error('"Open list" menu item not found');
  await new Promise(r => setTimeout(r, 800));

  // Rows read as name + symbol count ("stonk-positions49"), so the remainder
  // must be all digits: a bare prefix would also match longer list names.
  const picked = await evaluate(`
    (function() {
      var want = ${safeString(name)};
      var rows = document.querySelectorAll('[data-role="list-item"]');
      for (var i = 0; i < rows.length; i++) {
        var t = (rows[i].textContent || '').trim();
        if (t.indexOf(want) === 0 && /^\\d*$/.test(t.slice(want.length))) { rows[i].click(); return true; }
      }
      return false;
    })()
  `);
  if (!picked) {
    const c = await getClient();
    await c.Input.dispatchKeyEvent({ type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    await c.Input.dispatchKeyEvent({ type: 'keyUp', key: 'Escape', code: 'Escape' });
    throw new Error(`Watchlist "${name}" not found in the list picker.`);
  }

  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 300));
    const info = await getActiveListInfo();
    if (info?.name === name) return { success: true, action: 'switched', name, list_id: info.id, previous: before?.name ?? null };
  }
  return { success: false, action: 'not_verified', name, previous: before?.name ?? null };
}
