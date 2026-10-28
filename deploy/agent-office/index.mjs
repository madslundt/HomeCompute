import { readFile } from 'node:fs/promises';
import { OfficeActivity } from './activity.mjs';
import { officeServer } from './server.mjs';
import { readAuth } from './auth.mjs';
import { projectRoster } from './catalog.mjs';

// Native agent requests can activate another plugin registry in this process.
// Its hooks must observe the same state as the already running gateway service.
const RUNTIME = Symbol.for('homecompute.agent-office.runtime.v1');
const runtime = () => globalThis[RUNTIME] ??= {};

export default {
  id: 'homecompute-agent-office',
  name: 'HomeCompute agent office',
  register(api) {
    let ownedServer;
    // Observation callbacks return no policy result and never read conversation
    // bodies, tool arguments/results, session files, or workspace content.
    const observe = kind => (event, ctx) => { runtime().activity?.observe(kind, event, ctx); };
    api.on('model_call_started', observe('model'));
    api.on('before_tool_call', observe('tool'), { priority: -2000000 });
    api.on('after_tool_call', observe('model'));
    api.on('agent_end', observe('end'));
    api.registerService({
      id: 'homecompute-agent-office',
      async start() {
        const state = runtime();
        if (state.server) return;
        const bundled = JSON.parse(await readFile(new URL('./roster.json', import.meta.url), 'utf8'));
        // Native config is already supplied by the trusted plugin API. Project
        // only known role IDs and bounded display names; no instructions,
        // credentials, channel settings or tool policies enter the snapshot.
        const entries = api.config?.agents?.entries;
        const roster = entries && typeof entries === 'object' && !Array.isArray(entries)
          ? projectRoster({ agents: entries }) : bundled;
        state.activity = new OfficeActivity(roster);
        const publicOrigin = api.pluginConfig?.publicOrigin;
        const auth = publicOrigin ? await readAuth('/sandbox/.openclaw/agent-office-auth.json') : undefined;
        ownedServer = state.server = await officeServer(() => state.activity.snapshot(), { port: api.pluginConfig?.port ?? 18802, publicOrigin, auth });
      },
      async stop() {
        const state = runtime();
        if (!ownedServer || state.server !== ownedServer) return;
        await new Promise((resolve, reject) => ownedServer.close(error => error ? reject(error) : resolve()));
        state.server = undefined;
        state.activity = undefined;
        ownedServer = undefined;
      },
    });
  },
};
