import { register } from '../router.js';
import * as core from '../../core/portfolio.js';

const requirePlan = (opts, sub) => {
  if (!opts.plan) throw new Error(`Plan file required. Usage: tv portfolio ${sub} --plan plan.json`);
  return opts.plan;
};

register('portfolio', {
  description: 'Position plans (apply, page, verify)',
  subcommands: new Map([
    ['apply', {
      description: 'Sync the plan\'s watchlists, paged layout and entry-line indicator',
      options: { plan: { type: 'string', short: 'p', description: 'Plan JSON file' } },
      handler: (opts) => core.apply({ plan_path: requirePlan(opts, 'apply') }),
    }],
    ['page', {
      description: 'Flip to a page (next, prev, current or a number), optionally on a --feed',
      options: {
        plan: { type: 'string', short: 'p', description: 'Plan JSON file' },
        feed: { type: 'string', short: 'f', description: 'Feed name, or primary' },
      },
      handler: (opts, positionals) => core.page({
        plan_path: requirePlan(opts, 'page'),
        page: positionals[0],
        feed: opts.feed,
      }),
    }],
    ['verify', {
      description: 'Check the plan against the live state; --reload proves it was saved',
      options: {
        plan: { type: 'string', short: 'p', description: 'Plan JSON file' },
        reload: { type: 'boolean', description: 'Reload the layout tab first' },
      },
      handler: (opts) => core.verify({ plan_path: requirePlan(opts, 'verify'), reload: !!opts.reload }),
    }],
  ]),
});
