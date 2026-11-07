import { register } from '../router.js';
import * as core from '../../core/watchlist.js';

register('watchlist', {
  description: 'Watchlist tools (get, list, create, switch, add, add-bulk, remove)',
  subcommands: new Map([
    ['get', {
      description: 'Get watchlist symbols',
      handler: () => core.get(),
    }],
    ['list', {
      description: 'List all custom watchlists with their symbols',
      handler: () => core.listAll(),
    }],
    ['create', {
      description: 'Create a watchlist from symbols',
      handler: (opts, positionals) => {
        if (!positionals[0]) throw new Error('Name required. Usage: tv watchlist create my-list NASDAQ:AAPL NASDAQ:MSFT');
        return core.create({ name: positionals[0], symbols: positionals.slice(1) });
      },
    }],
    ['switch', {
      description: 'Make a watchlist active',
      handler: (opts, positionals) => {
        if (!positionals[0]) throw new Error('Name required. Usage: tv watchlist switch my-list');
        return core.switchList({ name: positionals.join(' ') });
      },
    }],
    ['add', {
      description: 'Add a symbol to the watchlist',
      handler: (opts, positionals) => {
        if (!positionals[0]) throw new Error('Symbol required. Usage: tv watchlist add AAPL');
        return core.add({ symbol: positionals[0] });
      },
    }],
    ['add-bulk', {
      description: 'Add multiple symbols to the watchlist',
      handler: (opts, positionals) => {
        if (!positionals.length) throw new Error('Symbols required. Usage: tv watchlist add-bulk AAPL MSFT');
        return core.addBulk({ symbols: positionals });
      },
    }],
    ['remove', {
      description: 'Remove one or more symbols from the watchlist',
      handler: (opts, positionals) => {
        if (!positionals.length) throw new Error('Symbols required. Usage: tv watchlist remove AAPL MSFT');
        return core.remove({ symbols: positionals });
      },
    }],
  ]),
});
