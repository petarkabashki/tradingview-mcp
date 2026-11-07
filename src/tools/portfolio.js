import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/portfolio.js';

export function registerPortfolioTools(server) {
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
