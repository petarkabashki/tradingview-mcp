import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/portfolio.js';
import { pnl } from '../core/portfolio_pnl.js';

export function registerPortfolioTools(server) {
  server.tool('portfolio_pnl', 'Unrealized P&L of the Trading 212 positions: per asset the amount, percent return, weight (percent of portfolio value) and contribution (P&L as percent of portfolio cost), plus totals. Prices come from TradingView\'s scanner; no chart is touched. Price returns only: FX fees and FX moves are not included.', {
    ingest: z.string().optional().describe('Folder holding the Trading 212 CSVs (default: T212_INGEST)'),
    feed: z.string().optional().describe('Price from a feed such as perp, falling back to the primary listing; primary by default'),
    base: z.string().optional().describe('Currency for totals and weights (default: the positions\' currency, or USD when mixed)'),
    prices: z.record(z.string(), z.coerce.number()).optional().describe('Prices to use instead of the scanner, by ticker, e.g. {"AAPL": 250.25}'),
  }, async ({ ingest, feed, base, prices }) => {
    try { return jsonResult(await pnl({ ingest: ingest ?? process.env.T212_INGEST, feed, base, prices })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  server.tool('portfolio_apply', 'Apply a position plan file: save its entry-line Pine indicator, sync its watchlists and single paged layout, and put the indicator on every pane. Plans come from the Python tooling in portfolio/.', {
    plan_path: z.string().describe('Path to the plan JSON file'),
  }, async ({ plan_path }) => {
    try { return jsonResult(await core.apply({ plan_path })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  server.tool('portfolio_page', 'Flip the paged layout to another page of positions or another price feed, adding the entry-line indicator to any pane that lacks it. Keeps the feed showing unless one is given.', {
    plan_path: z.string().describe('Path to the plan JSON file'),
    page: z.coerce.string().optional().describe('next, prev, current, or a 1-based page number (default: current)'),
    feed: z.string().optional().describe('Feed from the plan, e.g. boats or perp; primary returns to the normal listings'),
  }, async ({ plan_path, page, feed }) => {
    try { return jsonResult(await core.page({ plan_path, page, feed })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  server.tool('portfolio_verify', 'Check a position plan against the live layout, entry lines and watchlists. Flips through the pages to read them, then returns to the page that was showing', {
    plan_path: z.string().describe('Path to the plan JSON file'),
    reload: z.coerce.boolean().optional().describe('Reload the layout tab first, which proves the state was saved'),
  }, async ({ plan_path, reload }) => {
    try { return jsonResult(await core.verify({ plan_path, reload })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });
}
