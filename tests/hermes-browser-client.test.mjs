import assert from 'node:assert/strict';
import { withCdpRelay } from '../deploy/hermes/browser-client.mjs';
import { local, upgradeRequest, createRelayServer } from '../deploy/hermes/browser-relay.mjs';
import http from 'node:http';
import net from 'node:net';
import { once } from 'node:events';

const endpoint = 'ws://172.18.0.1:18810/devtools/browser/homecompute';
const reference = 'openshell:resolve:env:s123_BROWSER_TOKEN';
assert.deepEqual(withCdpRelay(['--session', 'test', '--cdp', endpoint, 'snapshot'], local),
  ['--session', 'test', '--cdp', local, 'snapshot']);
assert.deepEqual(withCdpRelay(['connect', endpoint], local), ['connect', local]);
assert.deepEqual(withCdpRelay(['--cdp=' + endpoint], local), ['--cdp=' + local]);
for (const other of ['ws://example.com/devtools/browser/homecompute', endpoint + '/other', endpoint + '?x=1']) {
  assert.deepEqual(withCdpRelay(['--cdp', other], local), ['--cdp', other]);
}
const headers = {'sec-websocket-key': 'AAAAAAAAAAAAAAAAAAAAAA==', host: 'evil.example', authorization: 'sensitive'};
const request = upgradeRequest(headers, reference);
assert(request.startsWith('GET http://172.18.0.1:18810/devtools/browser/homecompute?token=' + reference));
assert(!request.includes('evil.example') && !request.includes('sensitive'));
assert.throws(() => upgradeRequest(headers, 'raw-secret'), /projection/);
assert.throws(() => upgradeRequest({'sec-websocket-key': 'injected\r\nHeader: bad'}, reference), /handshake/);
assert.deepEqual(withCdpRelay(['--version'], local), ['--version']);
console.log('Hermes CDP transport confines upgrades and credentials to the exact owned endpoint');

let received = '';
const proxy = net.createServer(socket => {
  socket.once('data', data => {
    received = data.toString();
    socket.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');
    socket.on('data', bytes => socket.write(bytes));
  });
});
proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening');
const relay = createRelayServer(new URL(`http://127.0.0.1:${proxy.address().port}`), reference);
relay.listen(0, '127.0.0.1'); await once(relay, 'listening');
try {
  await new Promise((resolve, reject) => {
    const request = http.request({host: '127.0.0.1', port: relay.address().port,
      path: '/devtools/browser/homecompute', headers: {Connection: 'Upgrade', Upgrade: 'websocket', ...headers}}, response => reject(new Error(String(response.statusCode))));
    request.on('error', reject);
    request.on('upgrade', (response, socket) => {
      socket.on('error', reject);
      socket.once('data', data => { assert.equal(data.toString(), 'synthetic-cdp-frame'); socket.destroy(); resolve(); });
      socket.write('synthetic-cdp-frame');
    });
    request.end();
  });
  assert.equal(received, request);
  await new Promise((resolve, reject) => {
    const request = http.request({host: '127.0.0.1', port: relay.address().port,
      path: '/other', headers: {Connection: 'Upgrade', Upgrade: 'websocket', ...headers}});
    request.on('error', error => error.code === 'ECONNRESET' ? resolve() : reject(error));
    request.on('upgrade', () => reject(new Error('Arbitrary upgrade allowed'))); request.end();
  });
} finally { relay.close(); proxy.close(); }
console.log('Real sockets preserve CDP traffic through the enforced proxy and reject other paths');
