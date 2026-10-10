import test from 'node:test';
import assert from 'node:assert/strict';
import plugin from '../deploy/openclaw/broker-plugin/index.mjs';
import { requestTask } from '../deploy/openclaw/broker-plugin/transport.mjs';

const id = '0aa529dc-8933-4116-852e-7871f01cbe07';
const submit = { project: 'homecompute', issue_key: 'simulated:test-1', summary: 'Synthetic issue', context: 'No personal data' };
const response = value => new Response(JSON.stringify(value), { status: 200 });

test('runtime registers exactly the three optional manifest tools', () => {
  const registered = [];
  plugin.register({ registerTool: (tool, options) => registered.push({ tool, options }) });
  assert.deepEqual(registered.map(x => x.tool.name), ['homecompute_task_submit', 'homecompute_task_status', 'homecompute_task_cancel']);
  assert(registered.every(x => x.options.optional && x.tool.parameters.additionalProperties === false));
});

test('public status preserves bounded progress/results and only verified PR artifacts', async () => {
  const task = await requestTask('status', { task_id: id }, { token: 'synthetic', fetchImpl: async () => response({
    id, state: 'review', status: 'completed', approval_required: true, approval_kind: 'publication', progress: null,
    result: { ok: true, tests_passed: true, changed_files: 1, session_id: 'synthetic-session', files: [{ content: 'private' }], prompt: 'private' },
    artifacts: [{ kind: 'pull_request', url: 'https://github.com/example/project/pull/2', token: 'private' },
      { kind: 'pull_request', url: 'https://github.com/example/project/pull/2?secret=private' },
      { kind: 'evidence', url: '/private/evidence' }],
  }) });
  assert.equal(task.status, 'completed');
  assert.equal(task.approval_kind, 'publication');
  assert.deepEqual(task.result, { ok: true, tests_passed: true, changed_files: 1, session_id: 'synthetic-session' });
  assert.deepEqual(task.artifacts, [{ kind: 'pull_request', url: 'https://github.com/example/project/pull/2' }]);
  assert(!JSON.stringify(task).includes('private'));
});

test('submission fixes the origin, auth, route and rejects redirects; strips private data', async () => {
  const task = await requestTask('submit', submit, { token: 'synthetic-token', fetchImpl: async (url, opts) => {
    assert.equal(url, 'http://broker:8080/tasks');
    assert.equal(opts.headers.Authorization, 'Bearer synthetic-token');
    assert.equal(opts.redirect, 'error');
    assert.equal(opts.method, 'POST');
    assert.deepEqual(JSON.parse(opts.body), submit);
    return response({ id, state: 'pending', context: 'private', worker_log: 'private', api_key: 'private', result: { pr_url: 'https://github.com/example/project/pull/1', prompt: 'private' } });
  } });
  assert.deepEqual(task, { id, state: 'pending', result: { pr_url: 'https://github.com/example/project/pull/1' } });
});

test('status and cancel use only the UUID-owned route without request bodies', async () => {
  for (const operation of ['status', 'cancel']) {
    await requestTask(operation, { task_id: id }, { token: 'synthetic', fetchImpl: async (url, opts) => {
      assert.equal(url, `http://broker:8080/tasks/${id}${operation === 'cancel' ? '/cancel' : ''}`);
      assert.equal(opts.method, operation === 'status' ? 'GET' : 'POST');
      assert.equal(opts.body, undefined);
      return response({ task: { id, state: operation === 'cancel' ? 'cancelled' : 'pending' } });
    } });
  }
});

test('input validation rejects URL/path injection, arbitrary fields and oversized context before networking', async () => {
  let calls = 0;
  const opts = { token: 'synthetic', fetchImpl: () => { calls++; throw Error('must not run'); } };
  for (const params of [{ task_id: '../../admin' }, { task_id: id, url: 'http://production' }, { task_id: id, operator_token: 'fake' }]) {
    await assert.rejects(requestTask('cancel', params, opts), /Invalid task arguments/);
  }
  await assert.rejects(requestTask('submit', { ...submit, project: 'https://evil' }, opts), /Invalid task arguments/);
  await assert.rejects(requestTask('submit', { ...submit, context: 'x'.repeat(16001) }, opts), /Invalid task arguments/);
  await assert.rejects(requestTask('submit', { ...submit, issue_key: 'nested/path' }, opts), /Invalid task arguments/);
  await assert.rejects(requestTask('submit', { ...submit, issue_key: 'x'.repeat(129) }, opts), /Invalid task arguments/);
  await assert.rejects(requestTask('submit', { ...submit, project: 'unsafe_project' }, opts), /Invalid task arguments/);
  assert.equal(calls, 0);
});

test('missing credentials and cancellation fail closed before sending', async () => {
  let calls = 0;
  const opts = { fetchImpl: () => { calls++; throw Error('must not run'); } };
  await assert.rejects(requestTask('status', { task_id: id }, { ...opts, token: '' }), /authentication is unavailable/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(requestTask('status', { task_id: id }, { ...opts, token: 'synthetic', signal: controller.signal }));
  assert.equal(calls, 0);
});

test('oversized, malformed and hostile broker errors do not expose raw output', async () => {
  for (const mock of [new Response('secret backend detail', { status: 500 }), new Response('x'.repeat(32769)), response({ id: 'unbounded-path' }), new Response('not-json')]) {
    await assert.rejects(requestTask('status', { task_id: id }, { token: 'synthetic', fetchImpl: async () => mock }), error => {
      assert(!error.message.includes('secret backend detail'));
      return /Task broker rejected|Invalid or oversized/.test(error.message);
    });
  }
});

test('status lists recent safe task metadata only when id is omitted', async () => {
  const tasks = await requestTask('status', {}, { token: 'synthetic', fetchImpl: async (url, opts) => {
    assert.equal(url, 'http://broker:8080/tasks');
    assert.equal(opts.method, 'GET');
    return response({ tasks: [{ id, state: 'pending', context: 'private' }] });
  } });
  assert.deepEqual(tasks, [{ id, state: 'pending' }]);
  await assert.rejects(requestTask('status', {}, { token: 'synthetic', fetchImpl: async () => response({ tasks: Array(101).fill({id}) }) }), /Invalid or oversized/);
});
