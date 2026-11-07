import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseOutput, pnlArgs } from '../src/core/portfolio_pnl.js';

test('pnlArgs builds the t212-tv pnl command', () => {
  const args = pnlArgs({ ingest: '/data/in', feed: 'perp', base: 'GBP', prices: { AAA: 12.5 } });
  const tail = args.slice(args.indexOf('t212-tv'));
  assert.deepEqual(tail, ['t212-tv', '--ingest', '/data/in', 'pnl', '--json', '--feed', 'perp', '--base', 'GBP', '--price', 'AAA=12.5']);
  assert.ok(args[args.indexOf('--project') + 1].endsWith('portfolio'));
});

test('pnlArgs needs an ingest folder', () => {
  assert.throws(() => pnlArgs({}), /ingest/);
});

test('parseOutput passes the report through and flags partial results', () => {
  const report = JSON.stringify({ base: 'USD', rows: [], missing: ['BBB'], total: {} });
  assert.deepEqual(parseOutput(2, report, ''), { success: true, partial: true, base: 'USD', rows: [], missing: ['BBB'], total: {} });
  assert.equal(parseOutput(0, report, '').partial, false);
});

test('parseOutput raises the CLI error on failure', () => {
  assert.throws(() => parseOutput(1, '', 'error: No CSV exports in /x\n'), /No CSV exports/);
});
