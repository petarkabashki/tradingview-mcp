/**
 * Apply a position plan to TradingView: a single layout that pages through the
 * positions, watchlists, and one tagged entry line per position. A tab holds
 * only so many charts (8 on Premium), so a page shows a slice of the positions
 * and `page` flips the same panes to the next slice.
 *
 * TradingView keeps a drawing with its pane and symbol, so a line comes back when
 * a pane shows that symbol again. A pair that has never been shown gets its line
 * as it appears: this is what keeps every page, page size and feed marked.
 *
 * This all runs in a single process on purpose. A separate `tv` process attaches
 * to the first chart tab it finds, so a loop of CLI calls could draw on whatever
 * layout happens to be first. Here we attach to the plan's layout, and every
 * write first confirms that layout is still the one attached.
 *
 * The plan is produced by the Python tooling in portfolio/ and contains no
 * orders: nothing here talks to a broker.
 */
import { evaluate, getChartApi, getClient, safeString } from '../connection.js';
import { readFile } from 'node:fs/promises';
import * as tab from './tab.js';
import * as pane from './pane.js';
import * as drawing from './drawing.js';
import * as watchlist from './watchlist.js';
import { layoutCurrent, layoutList, layoutSave } from './ui.js';

export const GRID_CAPACITY = {
  's': 1, '2h': 2, '2v': 2, '2-1': 3, '1-2': 3, '3h': 3, '3v': 3, '3s': 3,
  '4': 4, '4h': 4, '4v': 4, '4s': 4, '6': 6, '8': 8, '10': 10, '12': 12, '14': 14, '16': 16,
};

const GRID_ORDER = ['s', '2h', '3h', '4', '6', '8', '10', '12', '14', '16'];
const PRIMARY = 'primary';
const PRICE_EPSILON = 1e-6;
const TV_SYMBOL = /^[A-Z0-9_.-]+:[A-Z0-9_.!-]+$/;
const FEED_NAME = /^[a-z0-9_-]+$/;

function validateFeeds(plan, tickers) {
  const names = new Set();
  for (const feed of plan.feeds ?? []) {
    if (!FEED_NAME.test(feed.name || '') || feed.name === PRIMARY) throw new Error(`Bad feed name: ${feed.name}`);
    if (names.has(feed.name)) throw new Error(`Duplicate feed: ${feed.name}`);
    names.add(feed.name);
    if (!feed.watchlist?.name?.trim()) throw new Error(`Feed "${feed.name}" needs watchlist.name`);

    for (const [ticker, symbol] of Object.entries(feed.symbols ?? {})) {
      if (!tickers.has(ticker)) throw new Error(`Feed "${feed.name}" lists unknown ticker: ${ticker}`);
      if (!TV_SYMBOL.test(symbol)) throw new Error(`Bad symbol in feed "${feed.name}": ${symbol}`);
    }
    for (const symbol of feed.extras ?? []) {
      if (!TV_SYMBOL.test(symbol)) throw new Error(`Bad extra in feed "${feed.name}": ${symbol}`);
    }
  }
}

/** Throws on the first problem, so a bad plan never reaches the live app. */
export function validatePlan(plan) {
  if (plan?.version !== 3) throw new Error(`Unsupported plan version: ${plan?.version}`);
  if (!plan.watchlist?.name?.trim()) throw new Error('Plan needs watchlist.name');
  if (!plan.layout?.name?.trim()) throw new Error('Plan needs layout.name');
  if (!Number.isInteger(plan.page_size) || plan.page_size < 1 || plan.page_size > 16) {
    throw new Error(`Bad page_size: ${plan.page_size}`);
  }

  const { positions } = plan;
  if (!Array.isArray(positions) || positions.length === 0) throw new Error('Plan has no positions');

  const symbols = new Set();
  const tickers = new Set();
  for (const p of positions) {
    if (!TV_SYMBOL.test(p.tv_symbol || '')) throw new Error(`Bad tv_symbol: ${p.tv_symbol}`);
    if (symbols.has(p.tv_symbol)) throw new Error(`Duplicate position: ${p.tv_symbol}`);
    symbols.add(p.tv_symbol);
    if (!p.ticker?.trim()) throw new Error(`Position ${p.tv_symbol} needs a ticker`);
    if (tickers.has(p.ticker)) throw new Error(`Duplicate ticker: ${p.ticker}`);
    tickers.add(p.ticker);
    if (!Number.isFinite(p.entry_price) || p.entry_price <= 0) {
      throw new Error(`Bad entry_price for ${p.tv_symbol}: ${p.entry_price}`);
    }
  }
  validateFeeds(plan, tickers);

  const { color, width, style, tag } = plan.line || {};
  if (!/^#[0-9a-fA-F]{6}$/.test(color || '')) throw new Error(`Bad line.color: ${color}`);
  if (![1, 2, 3, 4].includes(width)) throw new Error(`Bad line.width: ${width}`);
  if (![0, 1, 2].includes(style)) throw new Error(`Bad line.style: ${style}`);
  if (!tag?.trim()) throw new Error('Plan needs line.tag');
  return plan;
}

/** Smallest grid holding `count` charts. */
export function pickGrid(count) {
  const grid = GRID_ORDER.find(g => GRID_CAPACITY[g] >= count);
  if (!grid) throw new Error(`${count} charts exceed the largest grid`);
  return grid;
}

export const pageCount = plan => Math.ceil(plan.positions.length / plan.page_size);

function findFeed(plan, name) {
  if (!name || name === PRIMARY) return null;
  const feed = (plan.feeds ?? []).find(f => f.name === name);
  if (feed) return feed;
  const known = [PRIMARY, ...(plan.feeds ?? []).map(f => f.name)].join(', ');
  throw new Error(`Unknown feed "${name}". This plan has: ${known}`);
}

/**
 * What one page shows. A position the feed does not carry stays on its primary
 * listing and is marked, so a page never has a gap.
 */
export function pageEntries(plan, page, feedName = null) {
  const feed = findFeed(plan, feedName);
  const start = page * plan.page_size;
  return plan.positions.slice(start, start + plan.page_size).map((p) => {
    const symbol = feed?.symbols?.[p.ticker];
    return {
      ticker: p.ticker,
      tv_symbol: symbol || p.tv_symbol,
      entry_price: p.entry_price,
      fallback: Boolean(feed) && !symbol,
    };
  });
}

/** Turn "next", "prev", "current" or a 1-based number into a page index, wrapping at the ends. */
export function resolvePage(request, current, total) {
  const asked = String(request ?? 'current').toLowerCase();
  if (asked === 'current') return current ?? 0;
  if (asked === 'next') return current === null ? 0 : (current + 1) % total;
  if (asked === 'prev') return current === null ? total - 1 : (current - 1 + total) % total;

  const number = Number(asked);
  if (!Number.isInteger(number) || number < 1 || number > total) {
    throw new Error(`Page must be next, prev, current or 1 to ${total}, got: ${request}`);
  }
  return number - 1;
}

/**
 * A chart can report a data feed in place of the listing it was asked for:
 * NASDAQ:AMAT comes back as BATS:AMAT on accounts using the Cboe feed. So panes
 * are compared by ticker; the exact listing is what the watchlist records.
 */
export function sameTicker(a, b) {
  const ticker = s => String(s ?? '').split(':').pop();
  return ticker(a) !== '' && ticker(a) === ticker(b);
}

const exchangeOf = s => String(s ?? '').split(':')[0];
const tickerOf = s => String(s ?? '').split(':').pop();

/** Exchanges only feeds use. BATS stands in for NASDAQ, so it never counts. */
export function feedExchanges(plan) {
  const primary = new Set(plan.positions.map(p => exchangeOf(p.tv_symbol)));
  const found = (plan.feeds ?? []).flatMap(f => Object.values(f.symbols ?? {}).map(exchangeOf));
  return [...new Set(found)].filter(e => !primary.has(e));
}

/**
 * Stricter than sameTicker, for feeds that share the ticker: STX on BOATS must
 * not pass for STX on NASDAQ, or a line could be read from the previous chart.
 * BOATS:STX can come back as BOATS_LS:STX, so exchanges match by prefix.
 */
export function onListing(live, want, exchanges = []) {
  if (!sameTicker(live, want)) return false;
  const isFeed = e => exchanges.some(x => e.startsWith(x));
  const [liveExchange, wantExchange] = [exchangeOf(live), exchangeOf(want)];
  return isFeed(wantExchange) ? liveExchange.startsWith(wantExchange) : !isFeed(liveExchange);
}

/** Work out the page and feed showing, from the visible panes. Needs no saved state. */
export function detectView(plan, visible) {
  const index = new Map();
  plan.positions.forEach((p, i) => {
    index.set(tickerOf(p.tv_symbol), i);
    for (const feed of plan.feeds ?? []) {
      const symbol = feed.symbols?.[p.ticker];
      if (symbol) index.set(tickerOf(symbol), i);
    }
  });

  const first = index.get(tickerOf(visible[0]));
  if (first === undefined) return { page: null, feed: null };
  const page = Math.floor(first / plan.page_size);

  const exchanges = feedExchanges(plan);
  const shows = (feed) => {
    const entries = pageEntries(plan, page, feed.name);
    return entries.some(e => !e.fallback)
      && entries.every((e, i) => onListing(visible[i], e.tv_symbol, exchanges));
  };
  return { page, feed: (plan.feeds ?? []).find(shows)?.name ?? null };
}

export function lineText(tag, price) {
  return `${tag} ${price}`;
}

/**
 * Decide what to change on one pane. Only lines carrying our tag are passed in,
 * so the user's own drawings never appear here.
 */
export function diffLines(existing, { price, color, width, style }) {
  const isWanted = l => Math.abs(l.price - price) < PRICE_EPSILON
    && (l.color || '').toLowerCase() === color.toLowerCase()
    && l.width === width && l.style === style;
  const keep = existing.find(isWanted) || null;
  return { keep, remove: existing.filter(l => l !== keep), create: !keep };
}

async function assertLayout(expected) {
  const cur = await layoutCurrent();
  if (cur.id !== expected.id || cur.name !== expected.name) {
    throw new Error(
      `Refusing to write: attached to "${cur.name}" (${cur.id}), expected "${expected.name}" (${expected.id}).`,
    );
  }
}

async function waitForLayoutId(name) {
  const deadline = Date.now() + 20000;
  let cur = null;
  while (Date.now() < deadline) {
    cur = await layoutCurrent().catch(() => null);
    if (cur?.id && cur?.name === name) return cur;
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`New tab did not settle on layout "${name}" (last saw "${cur?.name}", id ${cur?.id}).`);
}

/** Attach to the plan's layout, creating or opening it when needed. */
async function attachLayout({ name, id }) {
  if (!id) {
    const { layouts } = await layoutList();
    if (layouts.some(l => l.name === name)) {
      throw new Error(`Layout "${name}" already exists and this plan has no id for it; it was left unchanged.`);
    }
    await tab.newTab({ layout: 'new', name });
    const cur = await waitForLayoutId(name);
    return { id: cur.id, name, created: true };
  }

  const { tabs } = await tab.list();
  const open = tabs.find(t => t.chart_id === id);
  if (open) await tab.switchTab({ index: open.index });
  else await tab.newTab({ layout: name });

  // newTab matches titles by substring, so confirm we landed on the exact one.
  const expected = { id, name };
  await assertLayout(expected);
  return { ...expected, created: false };
}

async function readEntryLines(tag) {
  const api = await getChartApi();
  const lines = await evaluate(`
    (function() {
      var api = ${api};
      var tag = ${safeString(tag)};
      var out = [];
      api.getAllShapes().forEach(function(s) {
        if (s.name !== 'horizontal_line') return;
        var shape = api.getShapeById(s.id);
        var p = shape.getProperties() || {};
        if (String(p.text || '').indexOf(tag) !== 0) return;
        var pts = shape.getPoints() || [];
        out.push({
          id: s.id, text: p.text, price: pts[0] ? pts[0].price : null,
          color: p.linecolor, width: p.linewidth, style: p.linestyle,
        });
      });
      return out;
    })()
  `);
  return lines || [];
}

/**
 * Drawings can arrive just after the symbol does. Drawing into that gap would
 * duplicate a line, so an empty read gets a moment to fill.
 */
async function settledEntryLines(tag) {
  const deadline = Date.now() + 1500;
  let lines = await readEntryLines(tag);
  while (lines.length === 0 && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 300));
    lines = await readEntryLines(tag);
  }
  return lines;
}

async function activeSymbol() {
  const api = await getChartApi();
  return evaluate(`${api}.symbol()`);
}

async function waitForListing(symbol, exchanges) {
  const deadline = Date.now() + 10000;
  let cur = null;
  while (Date.now() < deadline) {
    cur = await activeSymbol();
    if (onListing(cur, symbol, exchanges)) return;
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`Pane did not switch to ${symbol} (still ${cur}).`);
}

/** TradingView refuses a grid over the plan's chart limit without raising an error. */
async function ensureGrid(grid) {
  const { layout } = await pane.list();
  if (layout === grid) return;
  await pane.setLayout({ layout: grid });
  const deadline = Date.now() + 5000;
  let cur = null;
  while (Date.now() < deadline) {
    cur = (await pane.list()).layout;
    if (cur === grid) return;
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`Grid ${grid} was not applied (still ${cur}). It may exceed this plan's charts per tab; lower the page size.`);
}

/** A list created in an earlier run is synced; only a new one is created. */
async function syncList({ name, id }, symbols) {
  if (id) return watchlist.sync({ id, name, symbols });
  return watchlist.create({ name, symbols });
}

const feedSymbols = feed => [...new Set([...Object.values(feed.symbols ?? {}), ...(feed.extras ?? [])])];

async function showEntry(index, entry, exchanges, target) {
  await assertLayout(target);
  await pane.setSymbol({ index, symbol: entry.tv_symbol });
  await waitForListing(entry.tv_symbol, exchanges);
}

async function syncPane(index, entry, line, exchanges, target) {
  await showEntry(index, entry, exchanges, target);

  const price = entry.entry_price;
  const report = { pane: index, ticker: entry.ticker, symbol: entry.tv_symbol, price, fallback: entry.fallback };
  const { keep, remove, create } = diffLines(await settledEntryLines(line.tag), { ...line, price });
  for (const stale of remove) await drawing.removeOne({ entity_id: stale.id });
  if (!create) return { ...report, line_id: keep.id, action: 'kept' };

  const drawn = await drawing.drawShape({
    shape: 'horizontal_line',
    point: { time: Math.floor(Date.now() / 1000), price },
    text: lineText(line.tag, price),
    overrides: {
      linecolor: line.color, linewidth: line.width, linestyle: line.style,
      showLabel: true, textcolor: line.color, horzLabelsAlign: 'right', vertLabelsAlign: 'bottom',
    },
  });
  if (!drawn.entity_id) throw new Error(`No line appeared on pane ${index} (${entry.tv_symbol}).`);
  return { ...report, line_id: drawn.entity_id, action: 'drawn' };
}

/** Put one page on the panes, repairing its entry lines. */
async function showPage(plan, page, feed, target) {
  const entries = pageEntries(plan, page, feed);
  await assertLayout(target);
  await ensureGrid(pickGrid(entries.length));

  const exchanges = feedExchanges(plan);
  const panes = [];
  for (const [index, entry] of entries.entries()) {
    panes.push(await syncPane(index, entry, plan.line, exchanges, target));
  }
  await assertLayout(target);
  await pane.focus({ index: 0 });
  return panes;
}

/** Read which page is on the panes. Hidden panes survive a grid change, so count from the grid. */
async function liveView(plan) {
  const { layout, panes } = await pane.list();
  const visible = panes.slice(0, GRID_CAPACITY[layout] ?? panes.length).map(p => p.symbol);
  return detectView(plan, visible);
}

export async function loadPlan(path) {
  return validatePlan(JSON.parse(await readFile(path, 'utf8')));
}

/** Compare one page with the plan. Flips panes to read them; draws nothing. */
async function inspectPage(plan, page, target) {
  const problems = [];
  const lines = [];
  const entries = pageEntries(plan, page);
  await assertLayout(target);
  await ensureGrid(pickGrid(entries.length));

  const exchanges = feedExchanges(plan);
  for (const [index, entry] of entries.entries()) {
    const label = `page ${page + 1} pane ${index} (${entry.tv_symbol})`;
    await showEntry(index, entry, exchanges, target);
    const found = await settledEntryLines(plan.line.tag);
    const { keep, remove } = diffLines(found, { ...plan.line, price: entry.entry_price });
    if (!keep) problems.push(`${label}: no matching entry line`);
    if (remove.length) problems.push(`${label}: ${remove.length} other tagged line(s)`);
    if (keep) lines.push({ page: page + 1, pane: index, symbol: entry.tv_symbol, line_id: keep.id, price: keep.price });
  }
  return { problems, lines };
}

async function inspectPages(plan, target) {
  const found = { problems: [], lines: [] };
  for (let page = 0; page < pageCount(plan); page += 1) {
    const { problems, lines } = await inspectPage(plan, page, target);
    found.problems.push(...problems);
    found.lines.push(...lines);
  }
  return found;
}

/** The primary list, then one list per feed. */
function plannedLists(plan) {
  return [
    { label: PRIMARY, ref: plan.watchlist, symbols: plan.positions.map(p => p.tv_symbol) },
    ...(plan.feeds ?? []).map(f => ({ label: f.name, ref: f.watchlist, symbols: feedSymbols(f) })),
  ].filter(l => l.symbols.length > 0);
}

async function inspectWatchlists(plan) {
  const { lists } = await watchlist.listAll();
  const problems = [];
  const ids = {};
  for (const { label, ref, symbols } of plannedLists(plan)) {
    const list = lists.find(l => (ref.id ? l.id === ref.id : l.name === ref.name));
    ids[label] = list?.id ?? null;
    if (!list) {
      problems.push(`watchlist "${ref.name}" not found`);
    } else if (symbols.length !== list.symbols.length || symbols.some(s => !list.symbols.includes(s))) {
      problems.push(`watchlist "${list.name}" holds ${list.symbols.join(', ')}`);
    }
  }
  return { problems, ids };
}

export async function apply({ plan_path }) {
  const plan = await loadPlan(plan_path);

  // Ids go in the result even on failure: without them a retry would refuse to
  // touch the lists and layout this same run just created.
  const result = {
    success: false, layout: null, watchlists: [], pages: pageCount(plan), panes: [], problems: [], save: null,
  };
  try {
    for (const { label, ref, symbols } of plannedLists(plan)) {
      const wl = await syncList(ref, symbols);
      result.watchlists.push({ feed: label, id: wl.id, name: ref.name, action: wl.action });
      if (!wl.success) throw new Error(`Watchlist did not match the plan: ${JSON.stringify(wl)}`);
    }

    const target = await attachLayout(plan.layout);
    result.layout = target;

    // Last page first, so the layout ends up on page 1.
    for (let page = pageCount(plan) - 1; page >= 0; page -= 1) {
      const panes = await showPage(plan, page, null, target);
      result.panes.push(...panes.map(p => ({ page: page + 1, ...p })));
    }

    await assertLayout(target);
    if (plan.watchlist.activate) {
      result.watchlist_activated = (await watchlist.switchList({ name: plan.watchlist.name })).action;
    }
    const saved = await layoutSave();
    result.save = { method: saved.method, confirmed: saved.confirmed };

    result.problems.push(...(await inspectWatchlists(plan)).problems);
    result.success = result.problems.length === 0;
  } catch (err) {
    result.error = err.message;
  }
  return result;
}

/**
 * Flip the layout to another page, or another feed. With no feed asked for, the
 * feed showing now is kept, so "next" does not drop out of an overnight view.
 */
export async function page({ plan_path, page: request = 'current', feed }) {
  const plan = await loadPlan(plan_path);
  if (!plan.layout.id) throw new Error(`Layout "${plan.layout.name}" has no id; run apply first.`);
  if (feed) findFeed(plan, feed);

  const target = await attachLayout(plan.layout);
  const view = await liveView(plan);
  const total = pageCount(plan);
  const index = resolvePage(request, view.page, total);
  const chosen = feed ? (feed === PRIMARY ? null : feed) : view.feed;

  const panes = await showPage(plan, index, chosen, target);
  const saved = await layoutSave();
  return {
    success: true,
    layout: target.name,
    page: index + 1,
    pages: total,
    page_size: plan.page_size,
    feed: chosen ?? PRIMARY,
    panes,
    on_primary: panes.filter(p => p.fallback).map(p => p.ticker),
    drawn: panes.filter(p => p.action === 'drawn').length,
    save: { method: saved.method, confirmed: saved.confirmed },
  };
}

async function reloadTab() {
  const c = await getClient();
  await c.Page.reload({});
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000));
    const ready = await evaluate(`
      (function() {
        try { return window.TradingViewApi._chartWidgetCollection.getAll().length; }
        catch (e) { return 0; }
      })()
    `).catch(() => 0);
    if (ready >= 1) return;
  }
  throw new Error('Layout did not come back after reloading the tab.');
}

/**
 * Check the live state against the plan, flipping through every page on the
 * primary listings and then back to the page that was showing. With `reload`,
 * reloads the layout's tab first, which proves the state was saved, not just
 * drawn. Feed charts are not checked: their lines appear as they are shown.
 */
export async function verify({ plan_path, reload = false }) {
  const plan = await loadPlan(plan_path);
  if (!plan.layout.id) throw new Error(`Layout "${plan.layout.name}" has no id; run apply first.`);

  const target = await attachLayout(plan.layout);
  if (reload) {
    await assertLayout(target);
    await reloadTab();
  }

  // Panes and drawings load after the widgets exist, so give a reload time.
  const deadline = Date.now() + (reload ? 20000 : 0);
  const view = await liveView(plan);
  let found;
  try {
    found = await inspectPages(plan, target);
    while (found.problems.length && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1500));
      found = await inspectPages(plan, target);
    }
  } finally {
    await showPage(plan, view.page ?? 0, view.feed, target).catch(() => null);
  }

  const lists = await inspectWatchlists(plan);
  const problems = [...found.problems, ...lists.problems];
  return {
    success: problems.length === 0,
    reloaded: reload,
    layout: target,
    pages: pageCount(plan),
    problems,
    lines: found.lines,
    watchlists: lists.ids,
  };
}
