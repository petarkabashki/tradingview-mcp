import { register } from '../router.js';
import * as core from '../../core/portfolio.js';

const requirePlan = (opts, sub) => {
  if (!opts.plan) throw new Error(`Plan file required. Usage: tv portfolio ${sub} --plan plan.json`);
  return opts.plan;
};

register('portfolio', {
  description: 'Position plans (apply, verify)',
  subcommands: new Map([
    ['apply', {
      description: 'Sync the plan\'s watchlist and layout, and draw its entry lines',
      options: { plan: { type: 'string', short: 'p', description: 'Plan JSON file' } },
      handler: (opts) => core.apply({ plan_path: requirePlan(opts, 'apply') }),
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
