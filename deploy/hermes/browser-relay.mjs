#!/usr/bin/env node
// Adapt clients without HTTP-proxy CDP support to OpenShell's enforced upgrade.
// The upstream, path and credential are fixed; this is not a general proxy.
import http from 'node:http';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const remote = 'ws://172.18.0.1:18810/devtools/browser/homecompute';
export const local = 'ws://127.0.0.1:18811/devtools/browser/homecompute';
const marker = 'homecompute-cdp-relay-v1';
export function upgradeRequest(headers, reference) {
  if (!/^openshell:resolve:env:[A-Za-z0-9_]+$/.test(reference ?? '')) throw new Error('Browser projection required');
  const key = headers['sec-websocket-key'];
  if (!/^[A-Za-z0-9+/]{22}==$/.test(key ?? '')) throw new Error('Invalid WebSocket handshake');
  return `GET http://172.18.0.1:18810/devtools/browser/homecompute?token=${reference} HTTP/1.1\r\n` +
    'Host: 172.18.0.1:18810\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n' +
    `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`;
}
function ready() {
  return new Promise(resolve => {
    const request = http.get('http://127.0.0.1:18811/health', {timeout: 500}, response => {
      let body = ''; response.on('data', c => body += c);
      response.on('end', () => resolve(response.statusCode === 200 && body === marker));
    });
    request.on('timeout', () => request.destroy()); request.on('error', () => resolve(false));
  });
}
export async function ensureRelay() {
  if (await ready()) return local;
  const env = {};
  for (const key of ['PATH', 'HTTP_PROXY', 'HTTPS_PROXY', 'BROWSER_TOKEN']) if (process.env[key]) env[key] = process.env[key];
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--serve'], {env, detached: true, stdio: 'ignore'});
  child.unref();
  for (let i = 0; i < 40; i++) {
    await new Promise(resolve => setTimeout(resolve, 100));
    if (await ready()) return local;
  }
  throw new Error('Dedicated browser relay unavailable');
}
export function createRelayServer(proxy, reference) {
  if (proxy.protocol !== 'http:' || proxy.username || proxy.password) throw new Error('OpenShell HTTP proxy required');
  upgradeRequest({'sec-websocket-key': 'AAAAAAAAAAAAAAAAAAAAAA=='}, reference);
  let clients = 0;
  const server = http.createServer((request, response) => {
    const ok = request.method === 'GET' && request.url === '/health';
    response.writeHead(ok ? 200 : 404); response.end(ok ? marker : '');
  });
  server.on('upgrade', (request, socket, head) => {
    if (request.method !== 'GET' || request.url !== '/devtools/browser/homecompute' || clients >= 8) return socket.destroy();
    let handshake;
    try { handshake = upgradeRequest(request.headers, reference); } catch { return socket.destroy(); }
    clients++;
    const upstream = net.connect({host: proxy.hostname, port: Number(proxy.port || 80)});
    const close = () => { socket.destroy(); upstream.destroy(); };
    socket.once('close', () => { clients--; upstream.destroy(); });
    socket.on('error', close); upstream.on('error', close); upstream.on('end', () => socket.end());
    socket.setTimeout(900000, close); upstream.setTimeout(900000, close);
    upstream.once('connect', () => { upstream.write(handshake); if (head.length) upstream.write(head); socket.pipe(upstream); upstream.pipe(socket); });
  });
  return server;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv[2] === '--serve') {
    const server = createRelayServer(new URL(process.env.HTTP_PROXY ?? process.env.HTTPS_PROXY ?? ''), process.env.BROWSER_TOKEN);
    server.on('error', () => process.exit(1));
    server.listen(18811, '127.0.0.1');
  }
  else if (process.argv[2] === '--ensure') console.log(await ensureRelay());
  else process.exit(2);
}
