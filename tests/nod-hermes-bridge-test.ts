// Run from tests/ in a fresh checkout of the Nod pin with the bridge source
// copied to src/server. DATABASE_URL must name a disposable Postgres database.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { setTimeout as pause } from 'node:timers/promises';
import { allowsDelegatedTool } from '../src/runtime/index.ts';
import Fastify from 'fastify';
import test from 'node:test';
import { registerLocalAccess } from '../src/server/access.ts';
import { ownerId } from '../src/server/config.ts';
import { initializeDatabase, pool } from '../src/server/db.ts';
import { registerDelegationBridge } from '../src/server/homecompute-hermes.ts';
import { boss, initializeThreads, startThreadWorkers } from '../src/server/thread.ts';

test('bridge isolates requests, supports safe retries and returns only the matching result', async (t) => {
  const secret = 'nod_' + 'A'.repeat(43);
  const openbotSecret = 'nod_' + 'B'.repeat(43);
  const openbotPath = '/mcp/openbot-nod';
  process.env.HOMECOMPUTE_NOD_BRIDGE_TOKEN = secret;
  process.env.HOMECOMPUTE_NOD_OPENBOT_TOKEN = openbotSecret;
  const app = Fastify();
  t.after(async () => {
    await app.close();
    await boss.stop({ graceful: false });
    await pool.end();
    delete process.env.HOMECOMPUTE_NOD_BRIDGE_TOKEN;
    delete process.env.HOMECOMPUTE_NOD_OPENBOT_TOKEN;
  });
  await initializeDatabase();
  await initializeThreads();
  registerLocalAccess(app, 4310, {
    key: 'owner_token',
    origin: 'https://nod.home.arpa',
    bearer: true,
  });
  registerDelegationBridge(app, () => true);

  async function rpc(
    method: string,
    params?: object,
    token = secret,
    path = '/mcp/hermes-nod',
    host = 'homecompute-capabilities.home.arpa',
  ) {
    return app.inject({
      method: 'POST',
      url: path,
      headers: { host, authorization: 'Bearer ' + token },
      payload: { jsonrpc: '2.0', id: 1, method, params },
    });
  }
  async function tool(name: string, args: object, client: 'hermes' | 'openbot' = 'hermes') {
    const credential = client === 'openbot' ? openbotSecret : secret;
    const response = await rpc(
      'tools/call', { name, arguments: args }, credential, `/mcp/${client}-nod`,
    );
    assert.equal(response.statusCode, 200);
    return JSON.parse(response.json().result.content[0].text);
  }

  assert.equal((await rpc('tools/list', {}, 'owner_token')).statusCode, 401);
  assert.equal(
    (await rpc('tools/list', {}, secret, '/api/state', 'nod.home.arpa')).statusCode,
    401,
  );
  assert.equal(
    (await rpc('tools/list', {}, secret, '/mcp/hermes-nod', 'nod.home.arpa')).statusCode,
    404,
  );
  assert.equal((await rpc('tools/list', {}, secret, openbotPath)).statusCode, 401);
  assert.equal((await rpc('tools/list', {}, openbotSecret)).statusCode, 401);
  assert.equal((await rpc('tools/list', {}, 'owner_token', openbotPath)).statusCode, 401);
  assert.equal(
    (await rpc('tools/list', {}, openbotSecret, openbotPath, 'nod.home.arpa')).statusCode, 404,
  );
  assert.equal(allowsDelegatedTool('manage_personal_context', { action: 'history_search' }), false);
  assert.equal(allowsDelegatedTool('browser', { action: 'upload' }), false);
  assert.equal(allowsDelegatedTool('send_message', { intent: 'update', text: 'Preparing' }), false);
  assert.equal(allowsDelegatedTool('send_message', { intent: 'final', text: 'Done' }), true);
  assert.deepEqual(
    (await rpc('tools/list')).json().result.tools.map((tool: any) => tool.name),
    ['nod_submit', 'nod_status'],
  );
  const request_id = randomUUID();
  const args = { request_id, task: 'Look up one synthetic result.' };
  const retry = await Promise.all([tool('nod_submit', args), tool('nod_submit', args)]);
  assert.deepEqual(retry, [
    { request_id, status: 'accepted' },
    { request_id, status: 'accepted' },
  ]);
  assert.equal(
    (await tool('nod_submit', { ...args, task: 'Different intent' })).status,
    'request_id_in_use',
  );
  assert.equal((await tool('nod_status', { request_id })).status, 'queued');
  assert.equal((await tool('nod_status', { request_id }, 'openbot')).status, 'not_found');
  assert.equal((await tool('nod_status', { request_id: randomUUID() })).status, 'not_found');
  const turns = (
    await pool.query(
      'SELECT id,thread_id,session_id,channel,source FROM turns WHERE source_id=$1',
      ['homecompute-hermes:' + request_id],
    )
  ).rows;
  assert.equal(turns.length, 1);
  const root = turns[0];
  assert.notEqual(root.thread_id, root.session_id);
  assert.equal(root.source, 'event');
  assert.equal(root.channel, 'web');

  await pool.query("UPDATE turns SET status='waiting', waiting_reason='approval' WHERE id=$1", [
    root.id,
  ]);
  assert.deepEqual(await tool('nod_status', { request_id }), {
    request_id,
    status: 'owner_action_required',
  });
  const continuationId = randomUUID();
  const unrelatedId = randomUUID();
  for (const [id, sessionId, parentId, result] of [
    [continuationId, root.session_id, root.id, 'Nod result'],
    [unrelatedId, root.thread_id, null, 'Private owner chat'],
  ]) {
    await pool.query(
      `INSERT INTO turns(id,owner_id,thread_id,session_id,source,prompt,status,parent_turn_id)
       VALUES($1,$2,$3,$4,'resume','test','succeeded',$5)`,
      [id, ownerId, root.thread_id, sessionId, parentId],
    );
    await pool.query(
      `INSERT INTO messages(id,owner_id,role,text,thread_id,turn_id) VALUES($1,$2,'assistant',$3,$4,$5)`,
      [randomUUID(), ownerId, result, root.thread_id, id],
    );
  }
  assert.deepEqual(await tool('nod_status', { request_id }), {
    request_id,
    status: 'succeeded',
    result: 'Nod result',
    truncated: false,
  });
  await pool.query(
    `INSERT INTO messages(id,owner_id,role,text,thread_id,turn_id,attachments,created_at)
      VALUES($1,$2,'assistant','Rich final',$3,$4,'[{}]',now()+interval '1 second')`,
    [randomUUID(), ownerId, root.thread_id, continuationId],
  );
  assert.deepEqual(await tool('nod_status', { request_id }), { request_id, status: 'view_in_nod' });

  let observed: unknown;
  let workspace: string | undefined;
  const unavailable = async () => {
    throw new Error('No external service is used in this test');
  };
  await startThreadWorkers({
    speech: { synthesize: unavailable },
    requestCredential: unavailable,
    integration: unavailable,
    manage: unavailable,
    browserStep: unavailable,
    memoryContext: unavailable,
    accountProfile: unavailable,
    learnedSkills: unavailable,
    runTurn: async (context) => {
      observed = {
        delegated: context.delegatedByHomeCompute,
        history: context.history.map((item) => item.text),
        memory: context.memory,
        account: context.account,
        allowSteering: Boolean(context.takeMessages),
        ownerThread: context.threadId === context.sessionId,
      };
      workspace = context.workspacePath;
      await assert.rejects(
        () => context.integration('CONNECTED_CLI', {}),
        /unavailable to delegated HomeCompute tasks/,
      );
      await assert.rejects(
        () => context.integration('SYNTHETIC', { input: { workspaceFile: 'synthetic.pdf' } }),
        /unavailable to delegated HomeCompute tasks/,
      );
      await assert.rejects(
        () => context.reply('[Synthetic file](/workspace/test/synthetic.pdf)'),
        /must be plain text/,
      );
      await context.reply('Worker result');
      return { status: 'succeeded' };
    },
  });
  // The same UUID can be used independently by both principals. Hermes must
  // retain its original result while OpenBot's separate run succeeds.
  const workerId = request_id;
  const workerArgs = { request_id: workerId, task: 'Return a worker result.' };
  assert.equal(
    (await tool('nod_submit', workerArgs, 'openbot')).status,
    'accepted',
  );
  assert.equal((await tool('nod_submit', workerArgs, 'openbot')).status, 'accepted');
  let status;
  for (let i = 0; i < 30; i++) {
    status = await tool('nod_status', { request_id: workerId }, 'openbot');
    if (status.status === 'succeeded') break;
    await pause(100);
  }
  assert.deepEqual(status, {
    request_id: workerId,
    status: 'succeeded',
    result: 'Worker result',
    truncated: false,
  });
  assert.deepEqual(observed, {
    delegated: true,
    history: [],
    memory: '',
    account: undefined,
    allowSteering: false,
    ownerThread: false,
  });
  assert.ok(workspace && !workspace.endsWith(root.thread_id));
  assert.deepEqual(await tool('nod_status', { request_id }), { request_id, status: 'view_in_nod' });
  const openbotTurns = await pool.query('SELECT session_id FROM turns WHERE source_id=$1', [
    'homecompute-openbot:' + workerId,
  ]);
  assert.equal(openbotTurns.rowCount, 1);
  assert.notEqual(openbotTurns.rows[0].session_id, root.session_id);
  await pool.query('UPDATE threads SET archived_at=now() WHERE id=$1', [root.thread_id]);
  assert.equal((await tool('nod_status', { request_id })).status, 'not_found');
});
