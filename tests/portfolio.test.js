import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { diffLines, lineText, sameTicker, validatePlan } from '../src/core/portfolio.js';

const line = { color: '#00C853', width: 4, style: 2, tag: 'T212 entry' };
const positions = [
  { tv_symbol: 'NASDAQ:AAA', entry_price: 10.5 },
  { tv_symbol: 'NYSE:BBB', entry_price: 20 },
];
const page = (overrides = {}) => ({ name: 'layout', id: null, grid: '2h', positions, ...overrides });
const plan = (overrides = {}) => ({
  version: 2,
  watchlist: { name: 'list', id: null },
  layouts: [page()],
  line,
  ...overrides,
});
const withPositions = (list) => plan({ layouts: [page({ positions: list })] });
const onChart = (price, overrides = {}) => ({ id: `id${price}`, price, ...line, ...overrides });

describe('validatePlan', () => {
  it('accepts a good plan', () => {
    assert.equal(validatePlan(plan()).version, 2);
  });

  it('rejects positions that do not fit the grid', () => {
    assert.throws(() => validatePlan(plan({ layouts: [page({ grid: 's' })] })), /do not fit/);
  });

  it('rejects an unknown grid', () => {
    assert.throws(() => validatePlan(plan({ layouts: [page({ grid: '5' })] })), /Unknown grid/);
  });

  it('rejects a bare ticker, which TradingView could map to any exchange', () => {
    assert.throws(() => validatePlan(withPositions([{ tv_symbol: 'AAA', entry_price: 1 }])), /Bad tv_symbol/);
  });

  it('rejects a symbol carrying script characters', () => {
    const bad = [{ tv_symbol: 'NASDAQ:A"+alert(1)', entry_price: 1 }];
    assert.throws(() => validatePlan(withPositions(bad)), /Bad tv_symbol/);
  });

  it('rejects duplicate positions', () => {
    const twice = [{ tv_symbol: 'NYSE:A', entry_price: 1 }, { tv_symbol: 'NYSE:A', entry_price: 2 }];
    assert.throws(() => validatePlan(withPositions(twice)), /Duplicate position/);
  });

  it('rejects one position spread over two layouts', () => {
    const layouts = [page({ name: 'one' }), page({ name: 'two' })];
    assert.throws(() => validatePlan(plan({ layouts })), /Duplicate position/);
  });

  it('rejects two layouts with one name', () => {
    const other = [{ tv_symbol: 'NYSE:CCC', entry_price: 1 }];
    const layouts = [page(), page({ positions: other })];
    assert.throws(() => validatePlan(plan({ layouts })), /Duplicate layout/);
  });

  it('accepts positions spread over several layouts', () => {
    const other = [{ tv_symbol: 'NYSE:CCC', entry_price: 1 }];
    const layouts = [page({ name: 'one' }), page({ name: 'two', grid: 's', positions: other })];
    assert.equal(validatePlan(plan({ layouts })).layouts.length, 2);
  });

  for (const entry_price of [0, -1, NaN, '10']) {
    it(`rejects entry price ${entry_price}`, () => {
      assert.throws(() => validatePlan(withPositions([{ tv_symbol: 'NYSE:A', entry_price }])), /Bad entry_price/);
    });
  }

  it('rejects an empty plan, so a sync can never blank a layout', () => {
    assert.throws(() => validatePlan(withPositions([])), /no positions/);
    assert.throws(() => validatePlan(plan({ layouts: [] })), /no layouts/);
  });

  it('rejects an unusable line style', () => {
    assert.throws(() => validatePlan(plan({ line: { ...line, width: 9 } })), /line.width/);
    assert.throws(() => validatePlan(plan({ line: { ...line, color: 'green' } })), /line.color/);
  });
});

describe('diffLines', () => {
  it('draws when nothing is there', () => {
    assert.deepEqual(diffLines([], { ...line, price: 10 }), { keep: null, remove: [], create: true });
  });

  it('keeps a matching line, so re-running adds nothing', () => {
    const existing = onChart(10);
    assert.deepEqual(diffLines([existing], { ...line, price: 10 }), { keep: existing, remove: [], create: false });
  });

  it('replaces a line at an old price', () => {
    const stale = onChart(9);
    assert.deepEqual(diffLines([stale], { ...line, price: 10 }), { keep: null, remove: [stale], create: true });
  });

  it('replaces a line whose style drifted', () => {
    const thin = onChart(10, { width: 1 });
    const diff = diffLines([thin], { ...line, price: 10 });
    assert.deepEqual(diff.remove, [thin]);
    assert.equal(diff.create, true);
  });

  it('removes duplicates and keeps one', () => {
    const first = onChart(10);
    const second = { ...onChart(10), id: 'dup' };
    const diff = diffLines([first, second], { ...line, price: 10 });
    assert.equal(diff.keep, first);
    assert.deepEqual(diff.remove, [second]);
  });

  it('matches colour regardless of case', () => {
    assert.equal(diffLines([onChart(10, { color: '#00c853' })], { ...line, price: 10 }).create, false);
  });
});

describe('sameTicker', () => {
  it('accepts a data feed standing in for the listing', () => {
    assert.equal(sameTicker('BATS:AMAT', 'NASDAQ:AMAT'), true);
  });

  it('rejects a pane still showing another stock', () => {
    assert.equal(sameTicker('BATS:TSLA', 'NASDAQ:AMAT'), false);
  });

  it('rejects a pane with no symbol', () => {
    assert.equal(sameTicker(undefined, ''), false);
  });
});

describe('lineText', () => {
  it('starts with the tag used to find the line again', () => {
    assert.equal(lineText('T212 entry', 175.56), 'T212 entry 175.56');
  });
});
