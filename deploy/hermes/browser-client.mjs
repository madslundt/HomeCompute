#!/usr/bin/env node
// Keep credential references out of durable CDP URLs. Only the exact owned
// browser endpoint receives the projected reference, at process launch time.
import { realpathSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { ensureRelay } from './browser-relay.mjs';

const endpoint = 'ws://172.18.0.1:18810/devtools/browser/homecompute';
export function withCdpRelay(args, relay) {
  const result = [...args];
  for (let i = 0; i < result.length; i++) {
    if (['--cdp', 'connect'].includes(result[i]) && result[i + 1] === endpoint) {
      result[++i] = relay;
    } else if (result[i] === '--cdp=' + endpoint) {
      result[i] = '--cdp=' + relay;
    }
  }
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  let args = process.argv.slice(2);
  if (args.some(value => value === endpoint || value === '--cdp=' + endpoint)) {
    const relay = await ensureRelay();
    args = withCdpRelay(args, relay);
  }
  const result = spawnSync('/usr/local/lib/homecompute-agent-browser-runtime/node_modules/.bin/agent-browser',
    args, { stdio: 'inherit', env: process.env });
  process.exit(result.status ?? 1);
}
