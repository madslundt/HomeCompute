import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { request } from 'node:http';
import { OfficeActivity, STALE_MS, demoSnapshot } from '../deploy/agent-office/activity.mjs';
import { projectRoster, toolActivity } from '../deploy/agent-office/catalog.mjs';
import { officeServer } from '../deploy/agent-office/server.mjs';
import plugin from '../deploy/agent-office/index.mjs';
import { configPatch } from '../deploy/agent-office/config-patch.mjs';
import { ROLE_DESTINATIONS, FURNITURE_BOUNDS, WALKWAY_SEGMENTS, resolveDestination, findPath, isWalkable, projectToScene } from '../deploy/agent-office/scene-layout.mjs';
import { WALK_SHEETS, WORK_SHEETS, robotSpriteFrame, walkSpriteFrame, walkDirection } from '../deploy/agent-office/public/walking.mjs';
import { characterPresence } from '../deploy/agent-office/public/presence.mjs';
import { passwordRecord, readAuth } from '../deploy/agent-office/auth.mjs';
import { verifyBundle } from '../scripts/setup-agent-office.mjs';

const root = new URL('../', import.meta.url);
const team = JSON.parse(await readFile(new URL('config/openclaw-team.json', root), 'utf8'));
const roster = projectRoster(team);
const ctx = (runId = 'one', agentId = 'engineer', extra = {}) => ({ agentId, runId, sessionKey: `agent:${agentId}:synthetic-session`, ...extra });

test('roster comes from canonical agent names without instructions or channel IDs', () => {
  assert.equal(roster.length, Object.keys(team.agents).length);
  assert.deepEqual(roster.map(agent => agent.name), Object.values(team.agents).map(agent => agent.name));
  assert.equal(new Set(roster.map(agent => agent.sprite)).size, roster.length);
  assert.ok(roster.every(agent => Object.keys(agent).sort().join(',') === 'color,id,name,role,sprite'));
});

test('native roster projects all supported roles while excluding private config and unknown identities', () => {
  const projected = projectRoster({ agents: {
    cook: { name: 'Hestia', instructions: 'private synthetic instruction', tools: { allow: ['private-tool'] } },
    administrator: { name: 'Themis' }, finance: { name: 'Plutus' }, family: { name: 'Gaia' },
    unknown: { name: 'Unsupported private agent' },
  } });
  assert.deepEqual(projected.map(agent => agent.name), ['Hestia', 'Themis', 'Plutus', 'Gaia']);
  assert.ok(projected.every(agent => Object.keys(agent).sort().join(',') === 'color,id,name,role,sprite'));
  assert.ok(!JSON.stringify(projected).includes('private'));
  assert.equal(projectRoster({ agents: { cook: { name: 'invalid\nname' } } })[0].name, 'Meals');
});

test('unobserved and expired activity are unknown; a live heartbeat cannot renew a run', () => {
  let now = 1000;
  const activity = new OfficeActivity(roster, () => now);
  assert.ok(activity.snapshot().agents.every(agent => agent.status === 'unknown'));
  activity.observe('model', {}, ctx());
  assert.equal(activity.snapshot().agents.find(agent => agent.id === 'engineer').status, 'working');
  now += STALE_MS + 1;
  assert.equal(activity.snapshot().agents.find(agent => agent.id === 'engineer').status, 'unknown');
  assert.deepEqual(activity.snapshot().events, []);
});

test('concurrent runs aggregate; one idle/failed run cannot hide another working run', () => {
  const activity = new OfficeActivity(roster);
  activity.observe('model', {}, ctx('one'));
  activity.observe('tool', {}, ctx('two', 'engineer', { toolName: 'sessions_yield' }));
  let agent = activity.snapshot().agents.find(agent => agent.id === 'engineer');
  assert.equal(agent.active_runs, 2);
  assert.equal(agent.status, 'working');
  activity.observe('end', { success: false }, ctx('one'));
  agent = activity.snapshot().agents.find(agent => agent.id === 'engineer');
  assert.equal(agent.active_runs, 1);
  assert.equal(agent.status, 'waiting');
  activity.observe('end', { success: true }, ctx('two'));
  assert.equal(activity.snapshot().agents.find(agent => agent.id === 'engineer').status, 'idle');
});

test('projection excludes raw prompts, results, errors, tool names and native session identity', () => {
  const activity = new OfficeActivity(roster);
  const secret = 'PRIVATE-CANARY-DO-NOT-PUBLISH';
  activity.observe('tool', { prompt: secret, params: { token: secret }, toolName: secret }, ctx(secret, 'researcher', { toolName: 'browser' }));
  activity.observe('end', { success: false, error: secret, messages: [secret] }, ctx(secret, 'researcher'));
  const output = JSON.stringify(activity.snapshot());
  assert.ok(!output.includes(secret));
  assert.ok(!output.includes('synthetic-session'));
  assert.equal(toolActivity(secret), 'thinking');
  assert.equal(toolActivity('homecompute__homecompute_task_status'), 'coding');
  assert.equal(toolActivity('homecompute__homecompute_storage_list'), 'inventory');
  assert.equal(toolActivity('homecompute__homecompute_calendar_events'), 'planning');
  assert.equal(toolActivity('read'), 'workspace');
  assert.equal(activity.snapshot().agents.find(agent => agent.id === 'researcher').status, 'error');
});

test('missing or inconsistent native identity cannot fabricate an observed agent', () => {
  const activity = new OfficeActivity(roster);
  for (const context of [{}, { agentId: 'engineer' }, ctx('one', 'unknown'), ctx('one', 'engineer', { sessionKey: 'agent:family:private' })]) {
    activity.observe('model', { agentId: 'engineer', params: ctx() }, context);
  }
  assert.ok(activity.snapshot().agents.every(agent => agent.status === 'unknown'));
});

test('sparse native model context derives only a known agent from its trusted session identity', () => {
  const activity = new OfficeActivity(roster);
  activity.observe('model', { runId: 'native-model-run' }, { sessionKey: 'agent:engineer:synthetic-native-model' });
  const agent = activity.snapshot().agents.find(a => a.id === 'engineer');
  assert.equal(agent.status, 'working');
  assert.equal(agent.active_runs, 1);
  assert.ok(!JSON.stringify(activity.snapshot()).includes('synthetic-native-model'));
});

test('auxiliary native model completion clears provisional work without inventing an agent outcome', () => {
  let now = 1000;
  const activity = new OfficeActivity(roster, () => now);
  const context = ctx('auxiliary-native-request', 'engineer');
  activity.observe('model', { callId: 'private-call-one' }, context);
  activity.observe('model_end', { callId: 'wrong-call' }, context);
  assert.equal(activity.snapshot().agents.find(a => a.id === 'engineer').status, 'working');
  activity.observe('model_end', { callId: 'private-call-one' }, context);
  assert.equal(activity.snapshot().agents.find(a => a.id === 'engineer').status, 'unknown');
  activity.observe('end', { success: true }, ctx('actual-agent-turn', 'engineer'));
  now = 2000;
  activity.observe('model', { callId: 'private-call-two' }, context);
  activity.observe('model_end', { callId: 'private-call-two' }, context);
  const agent = activity.snapshot().agents.find(a => a.id === 'engineer');
  assert.equal(agent.status, 'idle');
  assert.equal(agent.observed_at, 1000, 'only the actual agent end establishes the completed outcome');
  assert.ok(!JSON.stringify(activity.snapshot()).includes('private-call'));
});

test('request completion cannot clear another model request or native tool continuation', () => {
  const activity = new OfficeActivity(roster);
  const context = ctx('native-run', 'engineer');
  activity.observe('model', { callId: 'one' }, context);
  activity.observe('model', { callId: 'two' }, context);
  activity.observe('model_end', { callId: 'one' }, context);
  assert.equal(activity.snapshot().agents.find(a => a.id === 'engineer').active_runs, 1);
  activity.observe('tool', {}, { ...context, toolName: 'unknown-native-tool' });
  activity.observe('model_end', { callId: 'two' }, context);
  assert.equal(activity.snapshot().agents.find(a => a.id === 'engineer').status, 'working');
  activity.observe('continuation', {}, context);
  activity.observe('model_end', { callId: 'two' }, context);
  assert.equal(activity.snapshot().agents.find(a => a.id === 'engineer').status, 'working');
  activity.observe('end', { success: true }, context);
  assert.equal(activity.snapshot().agents.find(a => a.id === 'engineer').status, 'idle');
});

test('event retention is bounded and demo feed is explicitly synthetic', () => {
  const activity = new OfficeActivity(roster);
  for (let i = 0; i < 200; i++) {
    activity.observe('tool', {}, ctx(String(i), 'engineer', { toolName: 'read' }));
    activity.observe('end', { success: true }, ctx(String(i)));
  }
  assert.equal(activity.snapshot().events.length, 40);
  assert.equal(demoSnapshot(roster).mode, 'demo');
});

test('every standing destination and corridor stays clear of furniture, including Atlas at the table', () => {
  const activityNames = ['thinking', 'workspace', 'coordinating', 'research', 'coding', 'operations', 'planning', 'shopping', 'inventory', 'travel', 'cooking', 'administration', 'finance', 'family'];
  for (const id of Object.keys(ROLE_DESTINATIONS)) {
    const start = resolveDestination({ id, status: 'unknown' });
    for (const status of ['working', 'waiting', 'idle', 'unknown', 'error']) for (const activity of activityNames) {
      const destination = resolveDestination({ id, status, activity });
      assert.ok(isWalkable(destination), `${id}/${status}/${activity} has a floor destination`);
      const path = findPath(start, destination);
      assert.ok(path.length || start.join() === destination.join(), `${id}/${status}/${activity} has a connected arrival route`);
    }
  }
  for (const [from, to] of WALKWAY_SEGMENTS) for (let step = 0; step <= 20; step++) {
    const point = from.map((value, index) => value + (to[index] - value) * step / 20);
    assert.ok(isWalkable(point), `Corridor crosses furniture at ${point}`);
  }
  const [left, top, right, bottom] = FURNITURE_BOUNDS.meetingTableAndChairs;
  assert.ok(ROLE_DESTINATIONS.main[1] > bottom, 'Atlas stands on floor below the table and chairs');
  assert.equal(isWalkable([(left + right) / 2, (top + bottom) / 2]), false);
  assert.deepEqual(findPath([(left + right) / 2, (top + bottom) / 2], ROLE_DESTINATIONS.main), []);
  assert.deepEqual(findPath(ROLE_DESTINATIONS.family, ROLE_DESTINATIONS.main), [], 'Gaia uses a separate visual atrium graph');
  const roles = Object.keys(ROLE_DESTINATIONS);
  for (let i = 0; i < roles.length; i++) for (let j = i + 1; j < roles.length; j++) {
    for (const activity of ['thinking', 'research']) for (const first of ['unknown', 'idle', 'working']) for (const second of ['unknown', 'idle', 'working']) {
      const a = resolveDestination({ id: roles[i], status: first, activity });
      const b = resolveDestination({ id: roles[j], status: second, activity });
      assert.ok(Math.hypot(a[0] - b[0], a[1] - b[1]) > 8, `${roles[i]}/${first} and ${roles[j]}/${second} stand separately`);
    }
  }
});

test('only stationary working robots deploy tools, with distinct static state cues', () => {
  const symbols = new Set();
  for (const status of ['working', 'waiting', 'idle', 'unknown', 'error']) {
    symbols.add(characterPresence(status).symbol);
    for (let sprite = 0; sprite < 12; sprite++) {
      const planted = robotSpriteFrame(sprite, { status });
      assert.equal(WORK_SHEETS.includes(planted.asset), status === 'working');
      const traveling = robotSpriteFrame(sprite, { status, traveling: true });
      assert.ok(WALK_SHEETS.includes(traveling.asset), 'Travel never deploys work tools');
      assert.ok(traveling.column < 8);
    }
  }
  assert.equal(symbols.size, 5);
  assert.deepEqual(characterPresence('invalid'), characterPresence('unknown'));
  assert.deepEqual(projectToScene([50, 0]), [50, 20]);
  assert.deepEqual(projectToScene([50, 100]), [50, 98]);
});

test('all 12 characters have distinct leg phases and four directional planted poses', async () => {
  assert.equal(walkDirection(-1, 0), 'west');
  assert.equal(walkDirection(1, 0), 'east');
  assert.equal(walkDirection(0, -1), 'north');
  assert.equal(walkDirection(0, 1), 'south');
  assert.equal(walkDirection(0, 0, 'west'), 'west');
  for (let sprite = 0; sprite < 12; sprite++) for (const direction of ['south', 'west', 'east', 'north']) {
    const first = walkSpriteFrame(sprite, direction, true, 0);
    const second = walkSpriteFrame(sprite, direction, true, 150);
    const idle = walkSpriteFrame(sprite, direction, false, 0);
    assert.notEqual(first.column, second.column);
    assert.ok(idle.column >= 8 && idle.column < 12);
    assert.equal(first.row, sprite % 3);
    assert.equal(first.asset, WALK_SHEETS[Math.floor(sprite / 3)]);
    const workA = robotSpriteFrame(sprite, { direction, status: 'working', timeMs: 0 });
    const workB = robotSpriteFrame(sprite, { direction, status: 'working', timeMs: 450 });
    assert.notEqual(workA.column, workB.column);
    assert.equal(workA.asset, WORK_SHEETS[Math.floor(sprite / 3)]);
    assert.equal(workA.row, first.row);
  }
  for (const asset of [...WALK_SHEETS, ...WORK_SHEETS]) {
    const png = await readFile(new URL(`deploy/agent-office/public${asset}`, root));
    assert.equal(png.readUInt32BE(16), WORK_SHEETS.includes(asset) ? 1024 : 1536);
    assert.equal(png.readUInt32BE(20), 480);
    assert.equal(png[25], 6, 'Directional atlas keeps RGBA transparency');
  }
});

function http(port, path, headers = {}, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = request({ hostname: '127.0.0.1', port, path, method, headers }, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', value => body += value);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on('error', reject); req.end();
  });
}

test('HTTP server is loopback-only, read-only and rejects cross-site/rebinding/path traversal', async t => {
  const server = await officeServer(() => new OfficeActivity(roster).snapshot(), { port: 0 });
  t.after(() => new Promise(resolve => server.close(resolve)));
  const { port, address } = server.address();
  assert.equal(address, '127.0.0.1');
  const page = await http(port, '/');
  assert.equal(page.status, 200);
  assert.match(page.headers['content-security-policy'], /frame-ancestors 'none'/);
  assert.equal((await http(port, '/api/office')).status, 200);
  assert.equal((await http(port, '/api/office', { Host: 'evil.example' })).status, 403);
  assert.equal((await http(port, '/api/office', { Origin: 'https://evil.example' })).status, 403);
  assert.equal((await http(port, '/api/office', { 'Sec-Fetch-Site': 'cross-site' })).status, 403);
  assert.equal((await http(port, '/api/office', { 'X-HomeCompute-Office-Origin': 'https://agents.home.arpa' })).status, 403, 'A preview cannot be accidentally published by the proxy');
  assert.equal((await http(port, '/api/office', {}, 'POST')).status, 405);
  for (const path of ['/../config/openclaw-team.json', '/%2e%2e/config', '/roster.json', '/package.json']) assert.equal((await http(port, path)).status, 404);
  assert.equal((await http(port, '/catalog.mjs')).status, 200);
  assert.equal((await http(port, '/scene-layout.mjs')).status, 200);
  assert.equal((await http(port, '/walking.mjs')).status, 200);
  assert.equal((await http(port, '/presence.mjs')).status, 200);
  assert.equal((await http(port, '/favicon.svg')).headers['content-type'], 'image/svg+xml');
  assert.equal((await http(port, '/rooms.mjs')).status, 200);
  for (const asset of [...WALK_SHEETS, ...WORK_SHEETS]) assert.equal((await http(port, asset)).status, 200);
});

test('published origin requires operator credentials for UI, assets, and metadata', async t => {
  const auth = await passwordRecord('operator', 'synthetic-office-password');
  assert.throws(() => officeServer(() => ({}), { publicOrigin: 'https://agents.home.arpa' }));
  const server = await officeServer(() => new OfficeActivity(roster).snapshot(), { port: 0, publicOrigin: 'https://agents.home.arpa', auth });
  t.after(() => new Promise(resolve => server.close(resolve)));
  const { port } = server.address();
  const headers = { Origin: 'https://agents.home.arpa', 'X-HomeCompute-Office-Origin': 'https://agents.home.arpa' };
  for (const path of ['/', '/api/office', '/assets/office.png']) assert.equal((await http(port, path, headers)).status, 401);
  const invalid = 'Basic ' + Buffer.from('operator:wrong-password').toString('base64');
  assert.equal((await http(port, '/api/office', { ...headers, Authorization: invalid })).status, 401);
  const valid = 'Basic ' + Buffer.from('operator:synthetic-office-password').toString('base64');
  assert.equal((await http(port, '/api/office', { ...headers, Authorization: valid })).status, 200);
  assert.equal((await http(port, '/', { 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document' })).status, 401, 'Homepage navigation reaches sign-in');
  assert.equal((await http(port, '/api/office', { ...headers, Origin: 'https://foreign.home.arpa', Authorization: valid })).status, 403);
});

test('DNS, Homepage and private HTTPS path agree and retain a Caddy-only bridge', async () => {
  const dns = JSON.parse(await readFile(new URL('config/agent-office-dns.json', root), 'utf8'));
  assert.deepEqual(dns.records, [{ name: 'agents.home.arpa', type: 'A', value: '192.168.30.122', ttl: 300 }]);
  const homepage = await readFile(new URL('deploy/homepage/config/services.yaml', root), 'utf8');
  assert.match(homepage, /id: homecore-agent-office[\s\S]*?href: https:\/\/agents\.home\.arpa\//);
  const caddy = await readFile(new URL('deploy/control-plane/Caddyfile', root), 'utf8');
  assert.match(caddy, /agents\.home\.arpa \{\s*tls internal/);
  assert.match(caddy, /header_up X-HomeCompute-Office-Origin https:\/\/agents\.home\.arpa/);
  const nix = await readFile(new URL('modules/nixos/agent-office.nix', root), 'utf8');
  assert.match(nix, /-s 172\.28\.200\.2\/32 -d 172\.28\.200\.1\/32/);
  assert.ok(!nix.includes('allowedTCPPorts'));
  assert.match(nix, /TCP4:10\.77\.20\.2:18803/);
  const guest = await readFile(new URL('deploy/agent-office/guest-proxy.service', root), 'utf8');
  assert.match(guest, /TCP4-LISTEN:18803,bind=10\.77\.20\.2,reuseaddr,fork TCP4:127\.0\.0\.1:18802/);
});

test('plugin registers observation hooks without modifying agent policies', () => {
  const handlers = new Map();
  let service;
  plugin.register({ on: (name, fn) => handlers.set(name, fn), registerService: value => service = value });
  assert.deepEqual([...handlers.keys()], ['model_call_started', 'model_call_ended', 'before_tool_call', 'after_tool_call', 'agent_end']);
  for (const handler of handlers.values()) assert.equal(handler({ params: { private: 'data' } }, ctx()), undefined);
  assert.equal(service.id, 'homecompute-agent-office');
});

test('request-scoped plugin registrations observe the running service state across module instances', async () => {
  const key = Symbol.for('homecompute.agent-office.runtime.v1');
  const state = globalThis[key] = { activity: new OfficeActivity(roster) };
  try {
    const first = new Map(), second = new Map();
    let otherService;
    plugin.register({ on: (name, fn) => first.set(name, fn), registerService() {} });
    const reloaded = (await import('../deploy/agent-office/index.mjs?request-registry-test')).default;
    reloaded.register({ on: (name, fn) => second.set(name, fn), registerService: s => otherService = s });
    const context = ctx('request-scoped-run', 'engineer');
    const { agentId, ...sparse } = context;
    first.get('model_call_started')({}, sparse);
    assert.equal(state.activity.snapshot().agents.find(a => a.id === agentId).status, 'working');
    await otherService.stop();
    second.get('agent_end')({ success: true }, context);
    assert.equal(state.activity.snapshot().agents.find(a => a.id === agentId).status, 'idle');
    assert.equal(state.activity.snapshot().events.length, 2);
  } finally { delete globalThis[key]; }
});

test('native config patch preserves other plugin access and refuses policy bypass', () => {
  const path = '/sandbox/.openclaw/homecompute-agent-office/' + 'a'.repeat(64);
  const before = { plugins: { allow: ['homecompute-safety'], entries: { 'homecompute-safety': { enabled: true } }, load: { paths: ['/protected/safety'] } } };
  const original = JSON.stringify(before);
  const changes = configPatch(before, path);
  assert.equal(JSON.stringify(before), original);
  assert.deepEqual(changes[0].value, ['homecompute-safety', 'homecompute-agent-office']);
  assert.deepEqual(changes[1].value, ['/protected/safety', path]);
  assert.ok(!configPatch({}, path).some(change => change.path === 'plugins.allow'));
  assert.throws(() => configPatch({ plugins: { enabled: false } }, path));
  assert.throws(() => configPatch({ plugins: { deny: ['homecompute-agent-office'] } }, path));
  assert.throws(() => configPatch(before, '/tmp/untrusted'));
});

test('bundle is reproducible, includes only projected roster, and refuses overwrite', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'homecompute-office-test-'));
  try {
    const script = new URL('scripts/build-agent-office.mjs', root);
    const first = join(directory, 'first'), second = join(directory, 'second');
    for (const output of [first, second]) execFileSync(process.execPath, [script.pathname, '--output', output]);
    assert.equal(await readFile(join(first, 'bundle.json'), 'utf8'), await readFile(join(second, 'bundle.json'), 'utf8'));
    const built = JSON.parse(await readFile(join(first, 'roster.json'), 'utf8'));
    assert.deepEqual(built, roster);
    const verified = await verifyBundle(first);
    assert.ok(verified.files.has('auth.mjs'));
    assert.ok(verified.files.has('public/presence.mjs'));
    assert.ok(verified.files.has('public/favicon.svg'));
    const { writeFile, chmod } = await import('node:fs/promises');
    await writeFile(join(second, 'index.mjs'), 'tampered\n');
    await assert.rejects(() => verifyBundle(second), /digest mismatch/);
    const authPath = join(directory, 'auth.json');
    await writeFile(authPath, JSON.stringify(await passwordRecord('operator', 'synthetic-office-password')), { mode: 0o600 });
    assert.equal((await readAuth(authPath)).username, 'operator');
    await chmod(authPath, 0o644);
    await assert.rejects(() => readAuth(authPath), /unavailable or invalid/);
    assert.throws(() => execFileSync(process.execPath, [script.pathname, '--output', first], { stdio: 'pipe' }));
  } finally { await rm(directory, { recursive: true, force: true }); }
});
