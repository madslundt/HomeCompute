#!/usr/bin/env node
// Keep credential references out of durable CDP URLs. Only the exact owned
// browser endpoint receives the projected reference, at process launch time.
import { realpathSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const endpoint = 'ws://172.18.0.1:18810/devtools/browser/homecompute';
export function withCdpCredential(args, reference) {
  const result = [...args];
  const credential = () => {
    if (!/^openshell:resolve:env:[A-Za-z0-9_]+$/.test(reference ?? '')) {
      throw new Error('The dedicated browser requires its OpenShell credential projection');
    }
    return endpoint + '?token=' + reference;
  };
  for (let i = 0; i < result.length; i++) {
    if (['--cdp', 'connect'].includes(result[i]) && result[i + 1] === endpoint) {
      result[++i] = credential();
    } else if (result[i] === '--cdp=' + endpoint) {
      result[i] = '--cdp=' + credential();
    }
  }
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const args = withCdpCredential(process.argv.slice(2), process.env.BROWSER_TOKEN);
  const result = spawnSync('/usr/local/lib/homecompute-agent-browser-runtime/node_modules/.bin/agent-browser',
    args, { stdio: 'inherit', env: process.env });
  process.exit(result.status ?? 1);
}
