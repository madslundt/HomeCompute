import { readFile } from 'node:fs/promises';
import { OfficeActivity } from './activity.mjs';
import { officeServer } from './server.mjs';
import { readAuth } from './auth.mjs';

export default {
  id: 'homecompute-agent-office',
  name: 'HomeCompute agent office',
  register(api) {
    let activity;
    let server;
    // Observation callbacks return no policy result and never read conversation
    // bodies, tool arguments/results, session files, or workspace content.
    const observe = kind => (event, ctx) => { activity?.observe(kind, event, ctx); };
    api.on('model_call_started', observe('model'));
    api.on('before_tool_call', observe('tool'), { priority: -2000000 });
    api.on('after_tool_call', observe('model'));
    api.on('agent_end', observe('end'));
    api.registerService({
      id: 'homecompute-agent-office',
      async start() {
        const roster = JSON.parse(await readFile(new URL('./roster.json', import.meta.url), 'utf8'));
        activity = new OfficeActivity(roster);
        const publicOrigin = api.pluginConfig?.publicOrigin;
        const auth = publicOrigin ? await readAuth('/sandbox/.openclaw/agent-office-auth.json') : undefined;
        server = await officeServer(() => activity.snapshot(), { port: api.pluginConfig?.port ?? 18802, publicOrigin, auth });
      },
      async stop() {
        if (server) await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
        activity = undefined;
      },
    });
  },
};
