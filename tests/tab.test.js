import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { shellIndexForLayout } from '../src/core/tab.js';

const TITLES = ['TSLA▲ 358.22+4.23%/ Main', 'AAPL▼ 190.10−1.19%/ Main 2', 'New tab'];

describe('shellIndexForLayout', () => {
  it('finds a layout by the end of its title', () => {
    assert.equal(shellIndexForLayout(TITLES, 'Main 2'), 1);
  });

  it('does not take a longer name for a shorter one', () => {
    assert.equal(shellIndexForLayout(TITLES, 'Main'), 0);
    assert.equal(shellIndexForLayout(['AAPL/ Main 2'], 'Main'), null);
  });

  it('accepts a title that is only the name', () => {
    assert.equal(shellIndexForLayout(['Main'], 'Main'), 0);
  });

  it('refuses to guess when a layout is open twice', () => {
    assert.equal(shellIndexForLayout(['A/ Main', 'B/ Main'], 'Main'), null);
  });

  it('finds nothing without a name or a match', () => {
    assert.equal(shellIndexForLayout(TITLES, null), null);
    assert.equal(shellIndexForLayout(TITLES, 'Other'), null);
  });
});
