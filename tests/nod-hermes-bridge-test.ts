// Run from tests/ in a fresh checkout of the Nod pin with the bridge source
// copied to src/server. DATABASE_URL must name a disposable Postgres database.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import Fastify from 'fastify';
import test from 'node:test';
import { registerLocalAccess } from '../src/server/access.ts';
import { ownerId } from '../src/server/config.ts';
import { initializeDatabase, pool } from '../src/server/db.ts';
import { registerHermesBridge } from '../src/server/homecompute-hermes.ts';
import { boss, initializeThreads } from '../src/server/thread.ts';

test('bridge isolates requests, supports safe retries and returns only the matching result', async (t) => {
  const secret = 'nod_' + 'A'.repeat(43);
  process.env.HOMECOMPUTE_NOD_BRIDGE_TOKEN = secret;
  const app = Fastify();
  t.after(async () => {
    await app.close();
    await boss.stop({ graceful: false });
    await pool.end();
    delete process.env.HOMECOMPUTE_NOD_BRIDGE_TOKEN;
  });
  await initializeDatabase();
  await initializeThreads();
  registerLocalAccess(app, 4310, {
    key: 'owner_token',
    origin: 'https://nod.home.arpa',
    bearer: true,
  });
  registerHermesBridge(app, () => true);

  async function rpc(method: string, params?: object, token = secret, path = '/mcp/hermes-nod') {
    return app.inject({
      method: 'POST',
      url: path,
      headers: { host: 'nod.home.arpa', authorization: 'Bearer ' + token },
      payload: { jsonrpc: '2.0', id: 1, method, params },
    });
  }
  async function tool(name: string, args: object) {
    const response = await rpc('tools/call', { name, arguments: args });
    assert.equal(response.statusCode, 200);
    return JSON.parse(response.json().result.content[0].text);
  }

  assert.equal((await rpc('tools/list', {}, 'owner_token')).statusCode, 401);
  assert.equal((await rpc('tools/list', {}, secret, '/api/state')).statusCode, 401);
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
  await pool.query('UPDATE threads SET archived_at=now() WHERE id=$1', [root.thread_id]);
  assert.equal((await tool('nod_status', { request_id })).status, 'not_found');
});
