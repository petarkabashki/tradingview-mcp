import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/portfolio.js';

export function registerPortfolioTools(server) {
  server.tool('portfolio_apply', 'Apply a position plan file: sync its watchlist and layout, and draw one entry line per position. Plans come from the Python tooling in portfolio/.', {
    plan_path: z.string().describe('Path to the plan JSON file'),
  }, async ({ plan_path }) => {
    try { return jsonResult(await core.apply({ plan_path })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  server.tool('portfolio_verify', 'Check a position plan against the live layout, entry lines and watchlist without changing them', {
    plan_path: z.string().describe('Path to the plan JSON file'),
    reload: z.coerce.boolean().optional().describe('Reload the layout tab first, which proves the state was saved'),
  }, async ({ plan_path, reload }) => {
    try { return jsonResult(await core.verify({ plan_path, reload })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });
}
