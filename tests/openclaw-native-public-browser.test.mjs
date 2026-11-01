// Run against the exact installed runtime during browser qualification. Local
// dependency skips do not establish live browser or egress readiness.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { projectProtectedBrowserSchema } from '../deploy/openclaw/safety-plugin/browser-schema.mjs';

const dist = '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist';
const skip = !fs.existsSync(dist + '/browser-tool.schema-CSTr80M_.js');
const policy = { allowedHostnames: ['127.0.0.1'] };
const publicLookup = async () => [{ address: '93.184.216.34', family: 4 }];

test('protected form schema projects the actual pinned native schema', { skip }, async () => {
  const native = await import(pathToFileURL(dist + '/browser-tool.schema-CSTr80M_.js'));
  const schema = projectProtectedBrowserSchema(native.n(native.r({ evaluateEnabled: false })));
  assert.ok(schema.properties.action.enum.includes('act'));
  assert.ok(schema.properties.kind.enum.includes('fill'));
  assert.ok(schema.properties.kind.enum.includes('type'));
  assert.ok(!schema.properties.kind.enum.includes('evaluate'));
  assert.equal(schema.properties.request, undefined);
  assert.equal(schema.properties.paths, undefined);
  assert.equal(schema.properties.fields.items.additionalProperties, false);
  assert.deepEqual(schema.properties.profile.enum, ['homecompute']);
});

test('native policy admits public names without the explicit-false IP-only restriction', { skip }, async () => {
  const { M: check } = await import(pathToFileURL(dist + '/chrome-lc3LoS6t.js'));
  for (const url of ['https://survey.vendor.com/form', 'http://survey.vendor.com/form']) {
    await check({ url, ssrfPolicy: policy, lookupFn: publicLookup });
    await assert.rejects(check({ url, ssrfPolicy: { ...policy, dangerouslyAllowPrivateNetwork: false },
      lookupFn: publicLookup }), /IP-literal/);
  }
});

test('native public-name policy still rejects private and mixed DNS answers', { skip }, async () => {
  const { M: check } = await import(pathToFileURL(dist + '/chrome-lc3LoS6t.js'));
  for (const address of ['10.77.20.1', '169.254.169.254', '100.64.0.1', 'fd00::1', '::ffff:10.77.20.1']) {
    const privateRow = { address, family: address.includes(':') ? 6 : 4 };
    for (const rows of [[privateRow], [...await publicLookup(), privateRow]]) {
      await assert.rejects(check({ url: 'https://survey.vendor.com/form', ssrfPolicy: policy,
        lookupFn: async () => rows }), /private|internal|special-use/);
    }
  }
});
