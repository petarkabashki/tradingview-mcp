/**
 * Apply a position plan to TradingView: one watchlist, one or more layouts, and
 * one tagged entry line per position. TradingView plans cap the charts per tab
 * (8 on Premium), so a plan can spread its positions over several layouts.
 *
 * This all runs in a single process on purpose. A separate `tv` process attaches
 * to the first chart tab it finds, so a loop of CLI calls could draw on whatever
 * layout happens to be first. Here we attach to each of the plan's layouts, and
 * every write first confirms that layout is still the one attached.
 *
 * The plan is produced by the Python tooling in portfolio/ and contains no
 * orders: nothing here talks to a broker.
 */
import { evaluate, getChartApi, getClient, reconnectTo, safeString } from '../connection.js';
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

const PRICE_EPSILON = 1e-6;
const TV_SYMBOL = /^[A-Z0-9_.-]+:[A-Z0-9_.!-]+$/;

/** Throws on the first problem, so a bad plan never reaches the live app. */
export function validatePlan(plan) {
  if (plan?.version !== 2) throw new Error(`Unsupported plan version: ${plan?.version}`);
  if (!plan.watchlist?.name?.trim()) throw new Error('Plan needs watchlist.name');

  const layouts = plan.layouts;
  if (!Array.isArray(layouts) || layouts.length === 0) throw new Error('Plan has no layouts');

  const names = new Set();
  const seen = new Set();
  for (const layout of layouts) {
    if (!layout.name?.trim()) throw new Error('Plan needs a name for every layout');
    if (names.has(layout.name)) throw new Error(`Duplicate layout: ${layout.name}`);
    names.add(layout.name);

    const capacity = GRID_CAPACITY[layout.grid];
    if (!capacity) throw new Error(`Unknown grid: ${layout.grid}`);

    const positions = layout.positions;
    if (!Array.isArray(positions) || positions.length === 0) throw new Error(`Layout "${layout.name}" has no positions`);
    if (positions.length > capacity) {
      throw new Error(`${positions.length} positions do not fit grid ${layout.grid} (${capacity} panes)`);
    }

    for (const p of positions) {
      if (!TV_SYMBOL.test(p.tv_symbol || '')) throw new Error(`Bad tv_symbol: ${p.tv_symbol}`);
      if (seen.has(p.tv_symbol)) throw new Error(`Duplicate position: ${p.tv_symbol}`);
      seen.add(p.tv_symbol);
      if (!Number.isFinite(p.entry_price) || p.entry_price <= 0) {
        throw new Error(`Bad entry_price for ${p.tv_symbol}: ${p.entry_price}`);
      }
    }
  }

  const { color, width, style, tag } = plan.line || {};
  if (!/^#[0-9a-fA-F]{6}$/.test(color || '')) throw new Error(`Bad line.color: ${color}`);
  if (![1, 2, 3, 4].includes(width)) throw new Error(`Bad line.width: ${width}`);
  if (![0, 1, 2].includes(style)) throw new Error(`Bad line.style: ${style}`);
  if (!tag?.trim()) throw new Error('Plan needs line.tag');
  return plan;
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

async function activeSymbol() {
  const api = await getChartApi();
  return evaluate(`${api}.symbol()`);
}

async function waitForSymbol(symbol) {
  const deadline = Date.now() + 10000;
  let cur = null;
  while (Date.now() < deadline) {
    cur = await activeSymbol();
    if (sameTicker(cur, symbol)) return;
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
  throw new Error(`Grid ${grid} was not applied (still ${cur}). It may exceed this plan's charts per tab; lower max_charts.`);
}

async function syncWatchlist(plan, symbols) {
  const { name, id } = plan.watchlist;
  if (id) return watchlist.sync({ id, name, symbols });
  return watchlist.create({ name, symbols });
}

async function syncPane(index, position, line, target) {
  await assertLayout(target);
  await pane.setSymbol({ index, symbol: position.tv_symbol });
  await waitForSymbol(position.tv_symbol);

  const price = position.entry_price;
  const { keep, remove, create } = diffLines(await readEntryLines(line.tag), { ...line, price });
  for (const stale of remove) await drawing.removeOne({ entity_id: stale.id });
  if (!create) return { pane: index, symbol: position.tv_symbol, price, line_id: keep.id, action: 'kept' };

  const drawn = await drawing.drawShape({
    shape: 'horizontal_line',
    point: { time: Math.floor(Date.now() / 1000), price },
    text: lineText(line.tag, price),
    overrides: {
      linecolor: line.color, linewidth: line.width, linestyle: line.style,
      showLabel: true, textcolor: line.color, horzLabelsAlign: 'right', vertLabelsAlign: 'bottom',
    },
  });
  if (!drawn.entity_id) throw new Error(`No line appeared on pane ${index} (${position.tv_symbol}).`);
  return { pane: index, symbol: position.tv_symbol, price, line_id: drawn.entity_id, action: 'drawn' };
}

export async function loadPlan(path) {
  return validatePlan(JSON.parse(await readFile(path, 'utf8')));
}

const planSymbols = plan => plan.layouts.flatMap(l => l.positions.map(p => p.tv_symbol));

/** Compare one live layout with its part of the plan. Reports; never writes. */
async function inspectLayout(layout, line, target) {
  const problems = [];
  await assertLayout(target);

  const { panes } = await pane.list();
  const lines = [];
  for (const [index, position] of layout.positions.entries()) {
    const label = `${layout.name} pane ${index} (${position.tv_symbol})`;
    if (!sameTicker(panes[index]?.symbol, position.tv_symbol)) {
      problems.push(`${label}: shows ${panes[index]?.symbol}`);
      continue;
    }
    await pane.focus({ index });
    await waitForSymbol(position.tv_symbol);
    const found = await readEntryLines(line.tag);
    const { keep, remove } = diffLines(found, { ...line, price: position.entry_price });
    if (!keep) problems.push(`${label}: no matching entry line`);
    if (remove.length) problems.push(`${label}: ${remove.length} other tagged line(s)`);
    if (keep) lines.push({ pane: index, symbol: position.tv_symbol, line_id: keep.id, price: keep.price });
  }
  return { problems, lines };
}

async function inspectWatchlist(plan, id) {
  const symbols = planSymbols(plan);
  const { lists } = await watchlist.listAll();
  const list = lists.find(l => (id ? l.id === id : l.name === plan.watchlist.name));
  if (!list) return { problems: [`watchlist "${plan.watchlist.name}" not found`], id: null };
  const differs = symbols.length !== list.symbols.length || symbols.some(s => !list.symbols.includes(s));
  return { problems: differs ? [`watchlist "${list.name}" holds ${list.symbols.join(', ')}`] : [], id: list.id };
}

export async function apply({ plan_path }) {
  const plan = await loadPlan(plan_path);

  // Ids go in the result even on failure: without them a retry would refuse to
  // touch the list and layouts this same run just created.
  const result = { success: false, layouts: [], watchlist: null, panes: [], problems: [] };
  try {
    const wl = await syncWatchlist(plan, planSymbols(plan));
    result.watchlist = { id: wl.id, name: plan.watchlist.name, action: wl.action, activated: null };
    if (!wl.success) throw new Error(`Watchlist did not match the plan: ${JSON.stringify(wl)}`);

    for (const [page, layout] of plan.layouts.entries()) {
      const target = await attachLayout(layout);
      const record = { ...target, save: null };
      result.layouts.push(record);
      await assertLayout(target);
      await ensureGrid(layout.grid);

      for (const [index, position] of layout.positions.entries()) {
        result.panes.push({ layout: layout.name, ...await syncPane(index, position, plan.line, target) });
      }

      await assertLayout(target);
      if (plan.watchlist.activate) {
        result.watchlist.activated = (await watchlist.switchList({ name: plan.watchlist.name })).action;
      }
      const saved = await layoutSave();
      record.save = { method: saved.method, confirmed: saved.confirmed };

      result.problems.push(...(await inspectLayout(layout, plan.line, target)).problems);
    }

    result.problems.push(...(await inspectWatchlist(plan, wl.id)).problems);
    result.success = result.problems.length === 0;
  } catch (err) {
    result.error = err.message;
  }
  return result;
}

async function reloadTab(paneCount) {
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
    if (ready >= paneCount) return;
  }
  throw new Error('Layout did not come back after reloading the tab.');
}

/**
 * Check the live state against the plan. With `reload`, reloads the layout's
 * own tab first, which proves the state was saved, not just drawn.
 */
export async function verify({ plan_path, reload = false }) {
  const plan = await loadPlan(plan_path);
  const { tabs } = await tab.list();

  const problems = [];
  const lines = [];
  const layouts = [];
  for (const layout of plan.layouts) {
    if (!layout.id) throw new Error(`Layout "${layout.name}" has no id; run apply first.`);
    const target = { id: layout.id, name: layout.name };
    layouts.push(target);

    const open = tabs.find(t => t.chart_id === target.id);
    if (!open) throw new Error(`Layout "${target.name}" is not open in any tab.`);
    await reconnectTo(open.id);

    if (reload) {
      await assertLayout(target);
      await reloadTab(GRID_CAPACITY[layout.grid]);
    }

    // Panes and drawings load after the widgets exist, so give a reload time.
    const deadline = Date.now() + (reload ? 20000 : 0);
    let result = await inspectLayout(layout, plan.line, target);
    while (result.problems.length && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1500));
      result = await inspectLayout(layout, plan.line, target);
    }
    problems.push(...result.problems);
    lines.push(...result.lines);
  }

  const list = await inspectWatchlist(plan, plan.watchlist.id);
  problems.push(...list.problems);
  return { success: problems.length === 0, reloaded: reload, layouts, problems, lines, watchlist_id: list.id };
}
