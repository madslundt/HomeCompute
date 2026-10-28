import { readFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { projectRoster } from './catalog.mjs';
import { OfficeActivity, demoSnapshot } from './activity.mjs';
import { officeServer } from './server.mjs';

const { values } = parseArgs({ options: { demo: { type: 'boolean', default: false }, port: { type: 'string', default: '18802' } } });
const port = Number(values.port);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Port must be 1024–65535');
const roster = projectRoster(JSON.parse(await readFile(new URL('../../config/openclaw-team.json', import.meta.url), 'utf8')));
const activity = new OfficeActivity(roster);
const server = await officeServer(() => values.demo ? demoSnapshot(roster) : { ...activity.snapshot(), mode: 'unconnected' }, { port });
console.log(`Agent office: http://127.0.0.1:${port} (${values.demo ? 'synthetic demo' : 'unconnected; all activity unknown'})`);
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => server.close());
