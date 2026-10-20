import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const dist = '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist';
const guard = '/sandbox/.openclaw/extensions/homecompute-safety';
test('pinned native source-transform loader loads protected hook and DNS dependency without effects', {
  skip: !fs.existsSync(dist),
}, async () => {
  const loaderUrl = pathToFileURL(dist + '/plugin-module-loader-cache-C4l9L2gm.js').href;
  const { n: nativeLoader } = await import(loaderUrl);
  const entry = guard + '/index.mjs';
  const loaded = nativeLoader({ modulePath: entry, rootDir: guard, aliasMap: {}, importerUrl: loaderUrl })(entry);
  const plugin = loaded.default ?? loaded;
  const hooks = []; const services = [];
  plugin.register({ on: (...args) => hooks.push(args), registerService: service => services.push(service) });
  assert.equal(hooks.length, 1); assert.equal(services.length, 1);
  const policy = hooks[0][1];
  assert.equal(policy({ toolName: 'browser', params: { action: 'navigate', targetUrl: 'https://example.com/' } }).params.profile, 'homecompute');
  for (const params of [{ action: 'act', request: { kind: 'click', ref: 'nonexistent-qualification' } },
    { action: 'navigate', targetUrl: 'https://unlisted-site.com/' }, { action: 'navigate', targetUrl: 'https://10.77.20.1/' }]) {
    assert.equal(policy({ toolName: 'browser', params }).block, true);
  }
});
