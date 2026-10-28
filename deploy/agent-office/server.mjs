import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { authenticated } from './auth.mjs';

const FILES = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/style.css': ['style.css', 'text/css; charset=utf-8'],
  '/app.mjs': ['app.mjs', 'text/javascript; charset=utf-8'],
  '/walking.mjs': ['walking.mjs', 'text/javascript; charset=utf-8'],
  '/scene-layout.mjs': ['../scene-layout.mjs', 'text/javascript; charset=utf-8'],
  '/catalog.mjs': ['../catalog.mjs', 'text/javascript; charset=utf-8'],
  '/assets/office.png': ['assets/office.png', 'image/png'],
  '/assets/agents.png': ['assets/agents.png', 'image/png'],
  '/assets/walk-coordination.png': ['assets/walk-coordination.png', 'image/png'],
  '/assets/walk-operations.png': ['assets/walk-operations.png', 'image/png'],
  '/assets/walk-household.png': ['assets/walk-household.png', 'image/png'],
  '/assets/walk-planning.png': ['assets/walk-planning.png', 'image/png'],
};

export function officeServer(snapshot, { port = 18802, publicRoot = new URL('./public/', import.meta.url), publicOrigin, auth } = {}) {
  if ((publicOrigin !== undefined || auth !== undefined) && (publicOrigin !== 'https://agents.home.arpa' || !auth)) throw new Error('Published office requires its fixed HTTPS origin and operator authentication');
  const server = createServer(async (request, response) => {
    const headers = {
      'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer', 'Cross-Origin-Resource-Policy': 'same-origin',
      'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    };
    const send = (status, type, body, extra = {}) => { response.writeHead(status, { ...headers, 'Content-Type': type, ...extra }); response.end(body); };
    // Loopback plus exact Host/Origin checks prevent DNS rebinding and cross-site reads.
    const hosts = [`127.0.0.1:${server.address()?.port}`, `localhost:${server.address()?.port}`];
    const origins = [...hosts.map(host => `http://${host}`), ...(publicOrigin ? [publicOrigin] : [])];
    const navigation = request.method === 'GET' && request.headers['sec-fetch-mode'] === 'navigate' && request.headers['sec-fetch-dest'] === 'document';
    const proxyOrigin = request.headers['x-homecompute-office-origin'];
    if ((proxyOrigin && proxyOrigin !== publicOrigin) || (!publicOrigin && (request.headers['x-forwarded-host'] || request.headers['x-forwarded-proto']))
        || !hosts.includes(request.headers.host) || (request.headers.origin && !origins.includes(request.headers.origin))
        || (!navigation && request.headers['sec-fetch-site'] && !['same-origin', 'none'].includes(request.headers['sec-fetch-site']))) {
      send(403, 'text/plain', 'Local same-origin access required'); return;
    }
    if (request.method !== 'GET') { send(405, 'text/plain', 'Read-only'); return; }
    try {
      if (auth && !await authenticated(request.headers.authorization, auth)) {
        send(401, 'text/plain', 'Operator sign-in required', { 'WWW-Authenticate': 'Basic realm="HomeCompute agent office", charset="UTF-8"' }); return;
      }
      if (request.url === '/api/office') { send(200, 'application/json', JSON.stringify(snapshot())); return; }
      if (!Object.hasOwn(FILES, request.url)) { send(404, 'text/plain', 'Not found'); return; }
      const [name, type] = FILES[request.url];
      send(200, type, await readFile(new URL(name, publicRoot)));
    } catch { send(503, 'text/plain', 'Office unavailable'); }
  });
  server.requestTimeout = 5000;
  server.headersTimeout = 5000;
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}
