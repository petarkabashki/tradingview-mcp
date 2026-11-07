import { register } from '../router.js';
import * as core from '../../core/ui.js';

register('layout', {
  description: 'Layout tools (list, current, save, switch)',
  subcommands: new Map([
    ['list', {
      description: 'List saved chart layouts',
      handler: () => core.layoutList(),
    }],
    ['current', {
      description: 'Show the layout open in the attached tab',
      handler: () => core.layoutCurrent(),
    }],
    ['save', {
      description: 'Save the layout open in the attached tab',
      handler: () => core.layoutSave(),
    }],
    ['switch', {
      description: 'Switch to a saved layout by name or ID',
      handler: (opts, positionals) => {
        if (!positionals[0]) throw new Error('Layout name required. Usage: tv layout switch "My Layout"');
        return core.layoutSwitch({ name: positionals.join(' ') });
      },
    }],
  ]),
});
