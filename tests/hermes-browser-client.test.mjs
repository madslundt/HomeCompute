import assert from 'node:assert/strict';
import { withCdpCredential } from '../deploy/hermes/browser-client.mjs';

const endpoint = 'ws://172.18.0.1:18810/devtools/browser/homecompute';
const reference = 'openshell:resolve:env:s123_BROWSER_TOKEN';
assert.deepEqual(withCdpCredential(['--session', 'test', '--cdp', endpoint, 'snapshot'], reference),
  ['--session', 'test', '--cdp', endpoint + '?token=' + reference, 'snapshot']);
assert.deepEqual(withCdpCredential(['connect', endpoint], reference), ['connect', endpoint + '?token=' + reference]);
assert.deepEqual(withCdpCredential(['--cdp=' + endpoint], reference), ['--cdp=' + endpoint + '?token=' + reference]);
for (const other of ['ws://example.com/devtools/browser/homecompute', endpoint + '/other', endpoint + '?x=1']) {
  assert.deepEqual(withCdpCredential(['--cdp', other], reference), ['--cdp', other]);
}
assert.throws(() => withCdpCredential(['--cdp', endpoint], 'raw-secret'), /OpenShell credential projection/);
assert.deepEqual(withCdpCredential(['--version']), ['--version']);
console.log('Hermes CDP wrapper confines opaque credentials to the exact owned endpoint');
