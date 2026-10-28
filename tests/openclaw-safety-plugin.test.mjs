import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import http from 'node:http';
import { createHash } from 'node:crypto';
import plugin, { browserPolicy, coordinatorPolicy } from '../deploy/openclaw/safety-plugin/index.mjs';
import { createBrowserRelay } from '../deploy/openclaw/safety-plugin/browser-relay.mjs';

const call = params => browserPolicy({ toolName: 'browser', params });

test('browser fallback search uses one approved public authority with encoded query data', () => {
  const targetUrl='https://marginalia-search.com/search?query='+encodeURIComponent('NixOS & docs? æøå');
  assert.equal(call({action:'navigate',targetUrl}).params.targetUrl,targetUrl);
  assert.equal(call({action:'navigate',targetUrl:'https://marginalia-search.com.evil/search?query=x'}).block,true);
});

test('travel specialist can read approved public provider pages without interacting', () => {
  for (const host of ['www.afbudsrejser.dk', 'www.solfaktor.dk', 'www.spies.dk',
    'www.tui.dk', 'www.apollorejser.dk', 'www.sunweb.dk']) {
    const event = { toolName: 'browser', params: { action: 'navigate', targetUrl: `https://${host}/` } };
    assert.equal(browserPolicy(event)?.block, undefined, host);
    assert.equal(coordinatorPolicy(event, { agentId: 'travel', sessionKey: 'agent:travel:main' }), undefined);
    assert.equal(call({ action: 'navigate', targetUrl: `https://${host}.evil/` }).block, true);
  }
  for (const action of ['click', 'fill', 'evaluate', 'act']) {
    assert.equal(call({ action, targetUrl: 'https://www.solfaktor.dk/' }).block, true);
  }
});

test('trusted execution identity routes coordinator domain calls without restricting child authorization', () => {
  const ctx = role => ({ agentId: role, sessionKey: `agent:${role}:subagent:synthetic` });
  for (const name of ['shopping_list', 'storage_list', 'school_calendar', 'web_search', 'ha_propose', 'task_submit']) {
    const event = { toolName: `homecompute__homecompute_${name}`, params: { agentId: 'shopper', context: ctx('shopper') } };
    assert.equal(coordinatorPolicy(event, ctx('main')).block, true);
    assert.match(coordinatorPolicy(event, ctx('main')).blockReason, /sessions_spawn/);
    for (const role of ['personal', 'researcher', 'engineer', 'operator', 'shopper', 'keeper', 'travel',
      'cook', 'administrator', 'finance', 'family']) {
      assert.equal(coordinatorPolicy(event, ctx(role)), undefined);
    }
    for (const untrusted of [undefined, {}, { agentId: 'shopper' }, ctx('unknown'),
      { agentId: 'shopper', sessionKey: 'agent:main:main' }]) {
      assert.equal(coordinatorPolicy(event, untrusted).block, true);
    }
  }
  for (const toolName of ['read', 'write', 'memory_search', 'sessions_spawn', 'sessions_yield', 'exec']) {
    assert.equal(coordinatorPolicy({ toolName, params: {} }, ctx('main')), undefined);
  }
});

test('registered coordinator hook preserves browser safety vetoes for specialist calls', () => {
  const hooks = [];
  plugin.register({ on: (...args) => hooks.push(args), registerService() {}, registerGatewayMethod() {} });
  assert.ok(hooks.some(([, callback]) => callback === coordinatorPolicy));
  const blocked = (params, role) => hooks.some(([, callback]) => callback({ toolName: 'browser', params },
    { agentId: role, sessionKey: `agent:${role}:main` })?.block === true);
  assert.equal(blocked({ action: 'navigate', targetUrl: 'https://docs.openclaw.ai/' }, 'main'), true);
  assert.equal(blocked({ action: 'navigate', targetUrl: 'https://docs.openclaw.ai/' }, 'researcher'), false);
  for (const params of [{ action: 'act' }, { action: 'navigate', targetUrl: 'https://home.arpa/' },
    { action: 'snapshot', profile: 'user' }]) assert.equal(blocked(params, 'researcher'), true);
});

test('registered native before-tool hook pins every permitted action to the isolated gateway profile', () => {
  const hooks = [];
  const services = [];
  plugin.register({ on: (...args) => hooks.push(args), registerService: service => services.push(service), registerGatewayMethod() {} });
  assert.equal(services.length, 1);
  assert.equal(services[0].id, 'homecompute-browser-relay');
  assert.equal(hooks[0][0], 'before_tool_call');
  assert.equal(hooks[0][1], browserPolicy);
  assert.ok(hooks[0][2].priority < 0);
  for (const action of ['focus', 'close', 'snapshot', 'screenshot']) {
    assert.deepEqual(call({ action }).params, { action, profile: 'homecompute', target: 'host' });
  }
  for (const action of ['navigate']) {
    assert.equal(call({ action, targetUrl: 'https://docs.openclaw.ai/tools/browser' }).params.profile, 'homecompute');
  }
  assert.equal(browserPolicy({ toolName: 'memory_search', params: { query: 'preference' } }), undefined);
});

test('gateway relay forwards authenticated HTTP and real WebSocket traffic to one enforced authority', async () => {
  const forwarded = [];
  const token = 'qualification-token';
  const upstream = http.createServer((request, response) => {
    assert.equal(request.headers.host, '172.18.0.1:18800');
    assert.equal(request.headers.authorization, undefined);
    assert.equal(request.headers.cookie, undefined);
    if (new URL(request.url, 'http://fixed').searchParams.get('token') !== token) { response.writeHead(401); response.end(); return; }
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ Browser: 'qualification', observedPath: request.url }));
  });
  upstream.on('upgrade', (request, socket, head) => {
    assert.equal(request.headers.host, '172.18.0.1:18800');
    assert.equal(new URL(request.url, 'http://fixed').searchParams.get('token'), token);
    const accept = createHash('sha1').update(request.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    let buffered = head;
    socket.on('data', chunk => {
      buffered = Buffer.concat([buffered, chunk]);
      if (buffered.length < 6) return;
      const length = buffered[1] & 127;
      if (buffered.length < 6 + length) return;
      if ((buffered[0] & 15) === 8) { socket.end(Buffer.from([0x88, 0])); return; }
      assert.ok(buffered[1] & 128);
      const data = Buffer.from(buffered.subarray(6, 6 + length));
      for (let i = 0; i < data.length; i++) data[i] ^= buffered[2 + i % 4];
      socket.write(Buffer.concat([Buffer.from([0x81, data.length]), data]));
      buffered = buffered.subarray(6 + length);
    });
    socket.on('error', () => socket.destroy());
    socket.on('end', () => socket.destroy());
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const relay = createBrowserRelay({
    createServer: callback => http.createServer(callback),
    request(options) {
      forwarded.push(options);
      assert.equal(options.hostname, '172.18.0.1');
      assert.equal(options.port, 18800);
      assert.equal(options.headers.host, '172.18.0.1:18800');
      assert.equal(options.method, 'GET');
      return http.request({ ...options, hostname: '127.0.0.1', port: upstream.address().port });
    },
  });
  function request(path, method = 'GET') {
    return new Promise((resolve, reject) => {
      const req = http.request({ hostname: '127.0.0.1', port: 18800, path, method,
        headers: { authorization: 'never-forward', cookie: 'never-forward' }, agent: false }, response => {
        let body = ''; response.on('data', chunk => body += chunk);
        response.on('end', () => resolve({ status: response.statusCode, body }));
      });
      req.on('error', reject); req.end();
    });
  }
  try {
    await relay.start();
    assert.equal((await request('/json/version?token=' + token)).status, 200);
    assert.equal((await request('/json/version?token=incorrect')).status, 401);
    const before = forwarded.length;
    for (const path of ['http://private.example/json/version', '//private.example/json/version', '/private', '/json/version#fragment']) {
      assert.equal((await request(path)).status, 403);
    }
    assert.equal((await request('/json/version?token=' + token, 'POST')).status, 403);
    assert.equal(forwarded.length, before);
    const ws = new WebSocket('ws://127.0.0.1:18800/devtools/browser/qualification?token=' + token);
    const answer = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { ws.close(); reject(new Error('bounded websocket proof timed out')); }, 5000);
      ws.onopen = () => ws.send('research');
      ws.onmessage = event => { clearTimeout(timeout); resolve(event.data); };
      ws.onerror = () => { clearTimeout(timeout); reject(new Error('websocket forwarding failed')); };
    });
    assert.equal(answer, 'research');
    const closed = new Promise(resolve => { ws.onclose = resolve; });
    await relay.stop(); await closed;
    assert.equal(forwarded.at(-1).path, '/devtools/browser/qualification?token=' + token);
  } finally {
    await relay.stop();
    await new Promise(resolve => upstream.close(resolve));
  }
});

test('gateway relay binds only on service start, bounds clients and closes idle sockets on stop', async () => {
  class Socket extends EventEmitter {
    setTimeout(ms, callback) { this.timeout = ms; this.timeoutCallback = callback; return this; }
    destroy() { this.destroyed = true; this.emit('close'); }
  }
  const server = new EventEmitter();
  server.listen = (address, done) => { server.address = address; done(); };
  server.close = done => { server.closed = true; done(); };
  const relay = createBrowserRelay({ createServer() { return server; }, request() { throw new Error('unexpected request'); } });
  assert.equal(server.address, undefined);
  await relay.start();
  assert.deepEqual(server.address, { host: '127.0.0.1', port: 18800, exclusive: true });
  const clients = Array.from({ length: 17 }, () => new Socket());
  for (const client of clients) server.emit('connection', client);
  assert.equal(server.maxConnections, 16);
  assert.equal(clients[16].destroyed, true);
  assert.equal(clients[0].timeout, 120000);
  clients[0].timeoutCallback();
  assert.equal(clients[0].destroyed, true);
  await relay.stop();
  assert.equal(server.closed, true);
  assert.ok(clients.every(client => client.destroyed));
});

test('page interactions, authentication/imports, nested batches and all unknown actions are blocked', () => {
  for (const action of ['act', 'click', 'type', 'fill', 'evaluate', 'upload', 'dialog', 'importprofile',
    'status', 'tabs', 'open', 'start', 'stop', 'profiles', 'download', 'waitfordownload', 'console', 'requests', 'pdf', '', 'toString', '__proto__']) {
    assert.equal(call({ action, request: { kind: 'click', ref: '1' } }).block, true, action);
  }
  for (const params of [undefined, null, [], {}, { action: 'snapshot', request: { kind: 'evaluate', fn: 'fetch("x")' } },
    { action: 'open', targetUrl: 'https://docs.openclaw.ai/', kind: 'type', text: 'send' },
    { action: 'screenshot', path: '/sandbox/.openclaw/bin/homecompute-cli' },
    { action: 'snapshot', node: 'production' }, { action: 'snapshot', frame: 'private' }]) {
    assert.equal(call(params).block, true);
  }
});

test('alternate browser profiles, host routing and private or authenticated navigation are blocked', () => {
  for (const params of [{ action: 'snapshot', profile: 'user' }, { action: 'snapshot', profile: 'chrome' },
    { action: 'snapshot', target: 'node' }, { action: 'snapshot', target: 'sandbox' }]) assert.equal(call(params).block, true);
  for (const targetUrl of ['javascript:alert(1)', 'data:text/html,hi', 'file:///etc/passwd', 'http://openclaw.ai/',
    'https://user:password@openclaw.ai/', 'https://127.0.0.1/', 'https://[::1]/', 'https://172.18.0.1/',
    'https://home-core/', 'https://home-core.home.arpa/', 'https://service.local/', 'https://openclaw.ai:8443/',
    'https://openclaw.ai/\n', 'https://unlisted-public-site.com/', 'https://example.com.evil/', 'bad-url']) assert.equal(call({ action: 'navigate', targetUrl }).block, true, targetUrl);
  assert.equal(call({ action: 'navigate', url: 'https://openclaw.ai/', targetUrl: 'https://openclaw.ai/' }).block, true);
});

test('bounded observation arguments succeed while malformed arguments fail closed', () => {
  assert.equal(call({ action: 'snapshot', compact: true, snapshotFormat: 'ai', refs: 'aria', maxChars: 10000 }).params.compact, true);
  assert.equal(call({ action: 'screenshot', type: 'png', fullPage: true, targetId: 'public-tab' }).params.type, 'png');
  for (const params of [{ action: 'snapshot', maxChars: 999999 }, { action: 'snapshot', depth: -1 },
    { action: 'snapshot', compact: 'true' }, { action: 'screenshot', type: 'html' },
    { action: 'close', targetId: { command: 'submit' } }, { action: 'screenshot', ref: 'x\n' }]) {
    assert.equal(call(params).block, true);
  }
  const params = { action: 'snapshot' };
  Object.defineProperty(params, 'profile', { get() { throw new Error('untrusted getter'); } });
  assert.equal(call(params).block, true);
});
