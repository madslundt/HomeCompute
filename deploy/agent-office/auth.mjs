import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { scrypt, randomBytes, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';

const derive = promisify(scrypt);
export async function passwordRecord(username, password) {
  if (!/^[a-zA-Z0-9._-]{1,64}$/.test(username) || typeof password !== 'string' || password.length < 16 || password.length > 256) throw new Error('A username and 16–256 character password are required');
  const salt = randomBytes(16).toString('hex');
  return { username, salt, hash: (await derive(password, salt, 64)).toString('hex') };
}

export async function readAuth(path) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size > 2048 || info.mode & 0o077) throw new Error('Private regular credential file required');
    const record = JSON.parse(await file.readFile('utf8'));
    if (typeof record.username !== 'string' || typeof record.salt !== 'string' || typeof record.hash !== 'string'
        || !/^[a-zA-Z0-9._-]{1,64}$/.test(record.username) || !/^[a-f0-9]{32}$/.test(record.salt) || !/^[a-f0-9]{128}$/.test(record.hash)
        || Object.keys(record).sort().join() !== 'hash,salt,username') throw new Error('Invalid credential record');
    return record;
  } catch {
    throw new Error('Credential file unavailable or invalid');
  } finally { await file.close(); }
}

export async function authenticated(header, record) {
  if (typeof header !== 'string' || header.length > 1024 || !/^Basic [A-Za-z0-9+/]+=*$/.test(header)) return false;
  const value = Buffer.from(header.slice(6), 'base64').toString('utf8');
  const separator = value.indexOf(':');
  if (separator < 1 || value.slice(0, separator) !== record.username || value.length - separator > 257) return false;
  const actual = await derive(value.slice(separator + 1), record.salt, 64);
  return timingSafeEqual(actual, Buffer.from(record.hash, 'hex'));
}
