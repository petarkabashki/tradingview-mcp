/**
 * Unrealized P&L of the Trading 212 positions, computed by the Python tooling in
 * portfolio/ (`t212-tv pnl --json`). Prices come from TradingView's scanner, so
 * this needs no chart and changes nothing in the app.
 */
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const TIMEOUT_MS = 120_000;
// The CLI exits 2 when it printed a report but some positions had no price.
const PARTIAL = 2;

export function pnlArgs({ ingest, feed, base, prices = {} }) {
  if (!ingest) throw new Error('Pass ingest (the folder with the Trading 212 CSVs) or set T212_INGEST');
  const args = ['run', '--quiet', '--project', resolve(REPO_ROOT, 'portfolio'), 't212-tv', '--ingest', ingest, 'pnl', '--json'];
  if (feed) args.push('--feed', feed);
  if (base) args.push('--base', base);
  for (const [ticker, price] of Object.entries(prices)) args.push('--price', `${ticker}=${price}`);
  return args;
}

export function parseOutput(code, stdout, stderr) {
  if (code === 0 || code === PARTIAL) {
    const report = JSON.parse(stdout);
    return { success: true, partial: code === PARTIAL, ...report };
  }
  throw new Error((stderr || stdout).trim().slice(-500) || `t212-tv exited ${code}`);
}

export function pnl({ ingest = process.env.T212_INGEST, feed, base, prices } = {}) {
  const args = pnlArgs({ ingest, feed, base, prices });
  return new Promise((done, fail) => {
    execFile('uv', args, { timeout: TIMEOUT_MS, maxBuffer: 4 * 1024 * 1024 }, (err, stdout, stderr) => {
      const code = err ? (typeof err.code === 'number' ? err.code : null) : 0;
      if (code === null) return fail(new Error(`Could not run uv: ${err.message}`));
      try { done(parseOutput(code, stdout, stderr)); } catch (e) { fail(e); }
    });
  });
}
