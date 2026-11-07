import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  detectView, diffLines, feedExchanges, lineText, onListing, pageCount, pageEntries, pickGrid,
  resolvePage, sameTicker, validatePlan,
} from '../src/core/portfolio.js';

const line = { color: '#00C853', width: 4, style: 2, tag: 'T212 entry' };
const position = (ticker, price = 10, exchange = 'NASDAQ') => ({
  ticker, tv_symbol: `${exchange}:${ticker}`, entry_price: price,
});
const positions = [position('AAA', 10.5), position('BBB', 20, 'NYSE')];
const plan = (overrides = {}) => ({
  version: 3,
  watchlist: { name: 'list', id: null },
  layout: { name: 'layout', id: null },
  page_size: 2,
  positions,
  feeds: [],
  line,
  ...overrides,
});
const onChart = (price, overrides = {}) => ({ id: `id${price}`, price, ...line, ...overrides });

const five = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE'].map(t => position(t));
const boats = {
  name: 'boats',
  watchlist: { name: 'night' },
  symbols: { AAA: 'BOATS:AAA', BBB: 'BOATS:BBB', CCC: 'BOATS:CCC', DDD: 'BOATS:DDD' },
};
const perp = {
  name: 'perp',
  watchlist: { name: 'perps' },
  symbols: { AAA: 'BINANCE:AAAUSDT.P', BBB: 'BINANCE:BBBXUSDT.P' },
  extras: ['CME_MINI:ES1!'],
};
const paged = plan({ positions: five, feeds: [boats, perp] });

describe('validatePlan', () => {
  it('accepts a good plan', () => {
    assert.equal(validatePlan(plan()).version, 3);
  });

  it('accepts more positions than one page holds', () => {
    assert.equal(validatePlan(paged).positions.length, 5);
  });

  it('refuses a plan from before paging', () => {
    assert.throws(() => validatePlan(plan({ version: 2 })), /Unsupported plan version/);
  });

  for (const page_size of [0, 17, 2.5, '4']) {
    it(`rejects page size ${page_size}`, () => {
      assert.throws(() => validatePlan(plan({ page_size })), /Bad page_size/);
    });
  }

  it('rejects a plan with no layout', () => {
    assert.throws(() => validatePlan(plan({ layout: {} })), /layout.name/);
  });

  it('rejects a bare ticker, which TradingView could map to any exchange', () => {
    assert.throws(() => validatePlan(plan({ positions: [{ ticker: 'A', tv_symbol: 'AAA', entry_price: 1 }] })), /Bad tv_symbol/);
  });

  it('rejects a symbol carrying script characters', () => {
    const bad = [{ ticker: 'A', tv_symbol: 'NASDAQ:A"+alert(1)', entry_price: 1 }];
    assert.throws(() => validatePlan(plan({ positions: bad })), /Bad tv_symbol/);
  });

  it('rejects duplicate positions', () => {
    assert.throws(() => validatePlan(plan({ positions: [position('A', 1), position('A', 2)] })), /Duplicate position/);
  });

  it('rejects one ticker on two listings, since feeds key by ticker', () => {
    const twice = [position('A', 1), position('A', 2, 'NYSE')];
    assert.throws(() => validatePlan(plan({ positions: twice })), /Duplicate ticker/);
  });

  for (const entry_price of [0, -1, NaN, '10']) {
    it(`rejects entry price ${entry_price}`, () => {
      assert.throws(() => validatePlan(plan({ positions: [{ ...position('A'), entry_price }] })), /Bad entry_price/);
    });
  }

  it('rejects an empty plan, so a sync can never blank a layout', () => {
    assert.throws(() => validatePlan(plan({ positions: [] })), /no positions/);
  });

  it('rejects an unusable line style', () => {
    assert.throws(() => validatePlan(plan({ line: { ...line, width: 9 } })), /line.width/);
    assert.throws(() => validatePlan(plan({ line: { ...line, color: 'green' } })), /line.color/);
  });

  it('rejects a feed naming a ticker the plan does not hold', () => {
    const feeds = [{ ...boats, symbols: { ZZZ: 'BOATS:ZZZ' } }];
    assert.throws(() => validatePlan(plan({ feeds })), /unknown ticker/);
  });

  it('rejects a feed symbol carrying script characters', () => {
    const feeds = [{ ...boats, symbols: { AAA: 'BOATS:A"+alert(1)' } }];
    assert.throws(() => validatePlan(plan({ feeds })), /Bad symbol/);
  });

  it('rejects a feed called primary, which means the normal listings', () => {
    assert.throws(() => validatePlan(plan({ feeds: [{ ...boats, name: 'primary', symbols: {} }] })), /Bad feed name/);
  });

  it('rejects two feeds with one name', () => {
    const feeds = [{ ...boats, symbols: {} }, { ...boats, symbols: {} }];
    assert.throws(() => validatePlan(plan({ feeds })), /Duplicate feed/);
  });
});

describe('pages', () => {
  it('counts a partial last page', () => {
    assert.equal(pageCount(paged), 3);
  });

  it('picks the smallest grid for a partial page', () => {
    assert.deepEqual([1, 2, 3, 4, 5, 7, 8].map(pickGrid), ['s', '2h', '3h', '4', '6', '8', '8']);
  });

  it('refuses more charts than any grid holds', () => {
    assert.throws(() => pickGrid(17), /largest grid/);
  });

  it('slices the positions in order', () => {
    assert.deepEqual(pageEntries(paged, 1).map(e => e.tv_symbol), ['NASDAQ:CCC', 'NASDAQ:DDD']);
  });

  it('keeps the entry price when showing a feed', () => {
    assert.equal(pageEntries(plan({ feeds: [{ ...boats, symbols: { AAA: 'BOATS:AAA' } }] }), 0, 'boats')[0].entry_price, 10.5);
  });

  it('shows the feed symbol, and the primary listing where the feed has none', () => {
    const [aaa, bbb] = pageEntries(plan({ feeds: [{ ...perp, symbols: { AAA: 'BINANCE:AAAUSDT.P' } }] }), 0, 'perp');
    assert.deepEqual([aaa.tv_symbol, aaa.fallback], ['BINANCE:AAAUSDT.P', false]);
    assert.deepEqual([bbb.tv_symbol, bbb.fallback], ['NYSE:BBB', true]);
  });

  it('treats primary as no feed', () => {
    assert.equal(pageEntries(paged, 0, 'primary')[0].tv_symbol, 'NASDAQ:AAA');
  });

  it('names the known feeds when asked for an unknown one', () => {
    assert.throws(() => pageEntries(paged, 0, 'okx'), /This plan has: primary, boats, perp/);
  });
});

describe('resolvePage', () => {
  it('counts pages from one', () => {
    assert.equal(resolvePage('2', 0, 3), 1);
  });

  it('accepts a number as well as a string', () => {
    assert.equal(resolvePage(3, 0, 3), 2);
  });

  it('wraps past the last page', () => {
    assert.equal(resolvePage('next', 2, 3), 0);
  });

  it('wraps before the first page', () => {
    assert.equal(resolvePage('prev', 0, 3), 2);
  });

  it('stays put on current, and when nothing was asked', () => {
    assert.equal(resolvePage('current', 1, 3), 1);
    assert.equal(resolvePage(undefined, 1, 3), 1);
  });

  it('starts at the first page when the panes show something else', () => {
    assert.equal(resolvePage('next', null, 3), 0);
    assert.equal(resolvePage('current', null, 3), 0);
  });

  for (const bad of ['0', '4', '1.5', 'last', '']) {
    it(`rejects page "${bad}"`, () => {
      assert.throws(() => resolvePage(bad, 0, 3), /Page must be/);
    });
  }
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

describe('feedExchanges', () => {
  it('lists exchanges only feeds use', () => {
    assert.deepEqual(feedExchanges(paged), ['BOATS', 'BINANCE']);
  });

  it('leaves out an exchange a position trades on', () => {
    const feeds = [{ ...boats, symbols: { AAA: 'NYSE:AAA' } }];
    assert.deepEqual(feedExchanges(plan({ feeds })), []);
  });
});

describe('onListing', () => {
  const exchanges = ['BOATS', 'BINANCE'];

  it('accepts the stand-in for a primary listing', () => {
    assert.equal(onListing('BATS:STX', 'NASDAQ:STX', exchanges), true);
  });

  it('does not take the feed chart for the primary one', () => {
    assert.equal(onListing('BOATS_LS:STX', 'NASDAQ:STX', exchanges), false);
  });

  it('does not take the primary chart for the feed one', () => {
    assert.equal(onListing('BATS:STX', 'BOATS:STX', exchanges), false);
  });

  it('accepts the name BOATS reports for itself', () => {
    assert.equal(onListing('BOATS_LS:STX', 'BOATS:STX', exchanges), true);
  });

  it('accepts an exact perpetual', () => {
    assert.equal(onListing('BINANCE:STXXUSDT.P', 'BINANCE:STXXUSDT.P', exchanges), true);
  });

  it('rejects another ticker', () => {
    assert.equal(onListing('BATS:AMD', 'NASDAQ:STX', exchanges), false);
  });
});

describe('detectView', () => {
  it('finds the page from the first pane', () => {
    assert.deepEqual(detectView(paged, ['BATS:CCC', 'BATS:DDD']), { page: 1, feed: null });
  });

  it('finds a partial last page', () => {
    assert.deepEqual(detectView(paged, ['BATS:EEE']), { page: 2, feed: null });
  });

  it('recognises a feed on the panes', () => {
    assert.deepEqual(detectView(paged, ['BOATS_LS:CCC', 'BOATS_LS:DDD']), { page: 1, feed: 'boats' });
  });

  it('recognises a feed by its ticker', () => {
    assert.deepEqual(detectView(paged, ['BINANCE:AAAUSDT.P', 'BINANCE:BBBXUSDT.P']), { page: 0, feed: 'perp' });
  });

  it('counts a feed page holding a fallback', () => {
    const feeds = [{ ...perp, symbols: { AAA: 'BINANCE:AAAUSDT.P' } }];
    assert.deepEqual(detectView(plan({ feeds }), ['BINANCE:AAAUSDT.P', 'BATS:BBB']), { page: 0, feed: 'perp' });
  });

  it('reports nothing when the panes show something else', () => {
    assert.deepEqual(detectView(paged, ['BATS:TSLA', 'BATS:AAA']), { page: null, feed: null });
  });

  it('copes with a layout that has no panes', () => {
    assert.deepEqual(detectView(paged, []), { page: null, feed: null });
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

describe('lineText', () => {
  it('starts with the tag used to find the line again', () => {
    assert.equal(lineText('T212 entry', 175.56), 'T212 entry 175.56');
  });
});
