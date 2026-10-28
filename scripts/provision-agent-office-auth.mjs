#!/usr/bin/env node
// Derive a private verifier from an externally provisioned password. No secrets
// are generated into Git or printed; the plaintext input remains external.
import { open, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { parseArgs } from 'node:util';
import { passwordRecord } from '../deploy/agent-office/auth.mjs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const { values } = parseArgs({ options: { username: { type: 'string' }, 'password-file': { type: 'string' }, output: { type: 'string' } } });
if (!values.username || !values['password-file'] || !values.output?.startsWith('/')) throw new Error('Provide --username, --password-file and an absolute --output outside Git');
const root = fileURLToPath(new URL('../', import.meta.url));
if (resolve(values.output).startsWith(root)) throw new Error('Credential output must stay outside the source checkout');
const file = await open(values['password-file'], constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
try {
  const info = await file.stat();
  if (!info.isFile() || info.mode & 0o077 || info.size > 1024) throw new Error('Private regular password input required');
  const password = (await file.readFile('utf8')).replace(/\r?\n$/, '');
  const record = await passwordRecord(values.username, password);
  await writeFile(values.output, JSON.stringify(record) + '\n', { mode: 0o600, flag: 'wx' });
  console.log('Private password verifier created; preserve input in the external secret store.');
} finally { await file.close(); }
