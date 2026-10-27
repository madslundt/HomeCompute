import dgram from 'node:dgram';
import net from 'node:net';
import http from 'node:http';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export const RESEARCH_HOSTS = Object.freeze([
  'example.com', 'docs.openclaw.ai', 'docs.nvidia.com', 'docs.litellm.ai',
  'docs.n8n.io', 'www.home-assistant.io', 'developers.home-assistant.io',
  'nixos.org', 'wiki.nixos.org', 'nix.dev', 'search.nixos.org', 'github.com',
  'raw.githubusercontent.com', 'duckduckgo.com',
  'marginalia-search.com',
  'www.afbudsrejser.dk', 'www.solfaktor.dk', 'www.spies.dk',
  'www.tui.dk', 'www.apollorejser.dk', 'www.sunweb.dk',
]);
const HOSTS = new Set(RESEARCH_HOSTS);
const privateIPv4 = new net.BlockList();
const privateIPv6 = new net.BlockList();
for (const [address, bits] of [['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10],
  ['127.0.0.0', 8], ['169.254.0.0', 16], ['172.16.0.0', 12], ['192.0.0.0', 24],
  ['192.0.2.0', 24], ['192.88.99.0', 24], ['192.168.0.0', 16], ['198.18.0.0', 15],
  ['198.51.100.0', 24], ['203.0.113.0', 24], ['224.0.0.0', 3]]) privateIPv4.addSubnet(address, bits, 'ipv4');
for (const [address, bits] of [['::', 3], ['4000::', 2], ['8000::', 1], ['2001::', 32],
  ['2001:2::', 48], ['2001:10::', 28], ['2001:20::', 28], ['2001:db8::', 32],
  ['2002::', 16]]) privateIPv6.addSubnet(address, bits, 'ipv6');

export function publicAddress(address, family) {
  return !(family === 6 && address.includes('.')) && net.isIP(address) === family
    && !(family === 4 ? privateIPv4 : privateIPv6).check(address, family === 4 ? 'ipv4' : 'ipv6');
}

export function decodeQuestion(packet) {
  if (!Buffer.isBuffer(packet) || packet.length < 12 || packet.length > 4096
      || packet.readUInt16BE(4) !== 1 || packet.readUInt16BE(6) !== 0 || packet.readUInt16BE(8) !== 0
      || packet.readUInt16BE(10) > 1 || (packet.readUInt16BE(2) & 0xf800)) throw new Error('Invalid DNS query');
  const labels = [];
  let offset = 12;
  while (true) {
    if (offset >= packet.length) throw new Error('Invalid DNS name');
    const size = packet[offset++];
    if (!size) break;
    if (size > 63 || offset + size > packet.length) throw new Error('Invalid DNS label');
    const bytes = packet.subarray(offset, offset + size);
    if ([...bytes].some(byte => byte > 127)) throw new Error('Non-ASCII DNS name');
    const label = bytes.toString('ascii');
    if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label)) throw new Error('Invalid DNS label');
    labels.push(label.toLowerCase()); offset += size;
    if (offset - 12 > 254) throw new Error('DNS name budget');
  }
  if (!labels.length || offset + 4 > packet.length) throw new Error('Invalid DNS question');
  const type = packet.readUInt16BE(offset);
  const qclass = packet.readUInt16BE(offset + 2);
  const end = offset + 4;
  if (packet.readUInt16BE(10) === 0 && end !== packet.length) throw new Error('Unexpected DNS data');
  if (packet.readUInt16BE(10) === 1) {
    if (end + 11 > packet.length || packet[end] !== 0 || packet.readUInt16BE(end + 1) !== 41
        || end + 11 + packet.readUInt16BE(end + 9) !== packet.length) throw new Error('Invalid DNS extension');
  }
  return { hostname: labels.join('.'), type, qclass, bytes: packet.subarray(12, end) };
}

function addressBytes(address, family) {
  if (family === 4) return Buffer.from(address.split('.').map(Number));
  const [left, right] = address.split('::');
  const start = left ? left.split(':') : [];
  const finish = right ? right.split(':') : [];
  const words = right !== undefined ? [...start, ...Array(8 - start.length - finish.length).fill('0'), ...finish] : start;
  const bytes = Buffer.alloc(16);
  words.forEach((word, i) => bytes.writeUInt16BE(parseInt(word, 16), i * 2));
  return bytes;
}

export function responsePacket(query, question, code = 0, addresses = [], ttl = 60) {
  const header = Buffer.alloc(12);
  if (query.length >= 2) query.copy(header, 0, 0, 2);
  header.writeUInt16BE(0x8080 | (query.length >= 4 ? query.readUInt16BE(2) & 0x0100 : 0) | code, 2);
  header.writeUInt16BE(question ? 1 : 0, 4);
  const records = [];
  for (const row of addresses) {
    const bytes = addressBytes(row.address, row.family);
    const record = Buffer.alloc(12);
    record.writeUInt16BE(0xc00c, 0); record.writeUInt16BE(row.family === 4 ? 1 : 28, 2);
    record.writeUInt16BE(1, 4); record.writeUInt32BE(ttl, 6); record.writeUInt16BE(bytes.length, 10);
    records.push(record, bytes);
  }
  header.writeUInt16BE(addresses.length, 6);
  return Buffer.concat([header, question?.bytes ?? Buffer.alloc(0), ...records]);
}

export function createWorkerLookup(token, transport = http) {
  if (typeof token !== 'string' || token.length < 32 || token.length > 256) throw new Error('Browser credential required');
  return hostname => new Promise((resolve, reject) => {
    if (!HOSTS.has(hostname)) { reject(new Error('Research hostname required')); return; }
    const request = transport.get({ hostname: '127.0.0.1', port: 18800,
      path: '/json/public-dns?hostname=' + encodeURIComponent(hostname) + '&token=' + encodeURIComponent(token),
      timeout: 3000, agent: false }, response => {
      let body = '';
      response.on('data', chunk => { body += chunk; if (body.length > 4096) request.destroy(new Error('DNS response budget')); });
      response.on('error', () => reject(new Error('DNS response unavailable')));
      response.on('end', () => {
        try {
          if (response.statusCode !== 200) throw new Error('DNS response unavailable');
          resolve(JSON.parse(body));
        } catch { reject(new Error('DNS response unavailable')); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('DNS request timeout')));
    request.on('error', () => reject(new Error('DNS request unavailable')));
  });
}

export function createDnsEngine(lookup, now = Date.now) {
  const cache = new Map();
  const pending = new Map();
  async function resolve(hostname) {
    const cached = cache.get(hostname);
    if (cached && cached.expires > now()) return cached;
    if (pending.has(hostname)) return pending.get(hostname);
    if (pending.size >= 4) throw new Error('DNS query budget');
    const operation = Promise.resolve().then(() => lookup(hostname)).then(value => {
      if (value?.hostname !== hostname || value.ttl !== 60 || !Array.isArray(value.addresses)
          || !value.addresses.length || value.addresses.length > 16
          || value.addresses.some(row => !row || ![4, 6].includes(row.family) || !publicAddress(row.address, row.family))) throw new Error('Public DNS response required');
      const entry = { addresses: value.addresses, expires: now() + 60000 };
      cache.set(hostname, entry); return entry;
    }).finally(() => pending.delete(hostname));
    pending.set(hostname, operation); return operation;
  }
  return {
    async handle(packet) {
      let question;
      try { question = decodeQuestion(packet); }
      catch { return responsePacket(packet, undefined, 1); }
      if (!HOSTS.has(question.hostname) || question.qclass !== 1 || ![1, 28].includes(question.type)) return responsePacket(packet, question, 3);
      try {
        const value = await resolve(question.hostname);
        return responsePacket(packet, question, 0, value.addresses.filter(row => row.family === (question.type === 1 ? 4 : 6)),
          Math.max(1, Math.min(60, Math.floor((value.expires - now()) / 1000))));
      } catch { return responsePacket(packet, question, 2); }
    },
    clear() { cache.clear(); },
  };
}

export async function startInheritedDns(engine) {
  const udp = dgram.createSocket('udp4');
  const clients = new Set();
  let udpPending = 0;
  let closing = false;
  udp.on('message', (query, remote) => {
    if (remote.address !== '127.0.0.1' && remote.address !== '127.0.0.11' || udpPending >= 16 || query.length > 4096) return;
    udpPending++;
    engine.handle(query).then(answer => {
      if (!closing) udp.send(answer, remote.port, remote.address, () => {});
    }).catch(() => {}).finally(() => udpPending--);
  });
  udp.on('error', () => {});
  const tcp = net.createServer(client => {
    if (clients.size >= 16) { client.destroy(); return; }
    clients.add(client); client.once('close', () => clients.delete(client));
    client.on('error', () => client.destroy()); client.setTimeout(5000, () => client.destroy());
    let buffered = Buffer.alloc(0); let busy = 0;
    client.on('data', bytes => {
      buffered = Buffer.concat([buffered, bytes]);
      if (buffered.length > 8192) { client.destroy(); return; }
      while (buffered.length >= 2) {
        const size = buffered.readUInt16BE(0);
        if (size < 12 || size > 4096 || busy >= 4) { client.destroy(); return; }
        if (buffered.length < size + 2) break;
        const query = buffered.subarray(2, size + 2); buffered = buffered.subarray(size + 2); busy++;
        engine.handle(query).then(answer => {
          if (closing || client.destroyed) return;
          const length = Buffer.alloc(2); length.writeUInt16BE(answer.length); client.write(Buffer.concat([length, answer]));
        }).catch(() => client.destroy()).finally(() => busy--);
      }
    });
  });
  tcp.maxConnections = 16;
  try { await Promise.all([
    new Promise((resolve, reject) => { udp.once('error', reject); udp.bind({ fd: 3 }, resolve); }),
    new Promise((resolve, reject) => { tcp.once('error', reject); tcp.listen({ fd: 4 }, resolve); }),
  ]); } catch (error) {
    closing = true;
    try { udp.close(); } catch {}
    try { tcp.close(); } catch {}
    throw error;
  }
  return async () => {
    closing = true;
    for (const client of clients) client.destroy();
    engine.clear();
    await Promise.all([new Promise(resolve => udp.close(resolve)), new Promise(resolve => tcp.close(resolve))]);
  };
}

async function main() {
  if (process.argv.length !== 2 || process.getuid() !== 998 || process.getgid() !== 998) throw new Error('Operator bootstrap required');
  const config = JSON.parse(fs.readFileSync('/sandbox/.openclaw/openclaw.json', 'utf8'));
  const url = new URL(config.browser.profiles.homecompute.cdpUrl);
  if (url.origin !== 'http://127.0.0.1:18800' || url.pathname !== '/' || [...url.searchParams.keys()].join() !== 'token'
      || config.browser.ssrfPolicy.dangerouslyAllowPrivateNetwork !== false
      || config.browser.ssrfPolicy.allowedHostnames.length !== RESEARCH_HOSTS.length + 1
      || new Set(config.browser.ssrfPolicy.allowedHostnames).size !== RESEARCH_HOSTS.length + 1
      || !config.browser.ssrfPolicy.allowedHostnames.every(host => host === '127.0.0.1' || HOSTS.has(host))) throw new Error('Reviewed browser configuration required');
  const stop = await startInheritedDns(createDnsEngine(createWorkerLookup(url.searchParams.get('token'))));
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { stop().finally(() => process.exit(0)); });
  console.log(JSON.stringify({ ready: true, service: 'homecompute-public-dns', uid: process.getuid(), gid: process.getgid() }));
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => { console.error('Public DNS bootstrap refused'); process.exit(1); });
}
