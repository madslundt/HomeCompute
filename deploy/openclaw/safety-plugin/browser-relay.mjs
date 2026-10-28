import http from 'node:http';

const UPSTREAM = Object.freeze({ hostname: '172.18.0.1', port: 18800 });
const HOST = '172.18.0.1:18800';
const LIMIT = 16;
const IDLE_MS = 120000;

function allowed(request) {
  return request.method === 'GET' && typeof request.url === 'string'
    && request.url.length <= 16384
    && /^\/json(?:\/[^#\\\r\n]*)?$|^\/devtools\/[^#\\\r\n]+$/.test(request.url);
}

// Forward HTTP through the native managed proxy rather than a raw TCP tunnel.
// Proxyline patches gateway sockets; HTTP requests retain its endpoint policy.
// The fixed Host also matches the worker's enforced CDP endpoint authority.
export function createBrowserRelay(transport = http) {
  let server;
  const clients = new Set();
  const upstreams = new Set();
  const requests = new Set();
  function forward(request) {
    const headers = { ...request.headers, host: HOST };
    for (const key of ['authorization', 'cookie', 'proxy-authorization', 'content-length', 'transfer-encoding']) delete headers[key];
    const outbound = transport.request({ ...UPSTREAM, method: 'GET', path: request.url, headers, agent: false });
    requests.add(outbound);
    outbound.once('close', () => requests.delete(outbound));
    outbound.on('socket', socket => {
      upstreams.add(socket);
      socket.once('close', () => upstreams.delete(socket));
      socket.setTimeout(IDLE_MS, () => socket.destroy());
    });
    outbound.setTimeout(IDLE_MS, () => outbound.destroy());
    return outbound;
  }
  return {
    id: 'homecompute-browser-relay',
    async start() {
      if (server) throw new Error('Browser relay is already started');
      server = transport.createServer((request, response) => {
        if (!allowed(request) || requests.size >= LIMIT) { response.writeHead(403); response.end(); return; }
        let outbound;
        try { outbound = forward(request); }
        catch { response.writeHead(502); response.end(); return; }
        outbound.on('response', upstream => {
          const headers = { ...upstream.headers };
          for (const key of ['set-cookie', 'proxy-authenticate']) delete headers[key];
          response.writeHead(upstream.statusCode ?? 502, headers);
          upstream.on('error', () => response.destroy());
          upstream.pipe(response);
        });
        outbound.on('error', () => { if (!response.headersSent) response.writeHead(502); response.end(); });
        request.once('aborted', () => outbound.destroy());
        response.once('close', () => { if (!response.writableFinished) outbound.destroy(); });
        outbound.end();
      });
      server.maxConnections = LIMIT;
      server.maxHeadersCount = 64;
      server.headersTimeout = 10000;
      server.requestTimeout = 15000;
      server.on('connection', client => {
        if (clients.size >= LIMIT) { client.destroy(); return; }
        clients.add(client);
        client.once('close', () => clients.delete(client));
        client.on('error', () => client.destroy());
        client.setTimeout(IDLE_MS, () => client.destroy());
      });
      server.on('connect', (_request, client) => client.destroy());
      server.on('upgrade', (request, client, clientHead) => {
        if (!allowed(request) || !request.url.startsWith('/devtools/') || requests.size >= LIMIT
            || request.headers.upgrade?.toLowerCase() !== 'websocket') { client.destroy(); return; }
        let outbound;
        try { outbound = forward(request); }
        catch { client.destroy(); return; }
        outbound.on('upgrade', (response, upstream, upstreamHead) => {
          if (response.statusCode !== 101) { client.destroy(); upstream.destroy(); return; }
          const headers = ['HTTP/1.1 101 Switching Protocols'];
          for (const key of ['upgrade', 'connection', 'sec-websocket-accept', 'sec-websocket-protocol', 'sec-websocket-extensions']) {
            if (typeof response.headers[key] === 'string') headers.push(`${key}: ${response.headers[key]}`);
          }
          client.write(headers.join('\r\n') + '\r\n\r\n');
          if (upstreamHead.length) client.write(upstreamHead);
          if (clientHead.length) upstream.write(clientHead);
          const close = () => { client.destroy(); upstream.destroy(); };
          client.once('close', close); upstream.once('close', close);
          upstream.on('error', close);
          client.pipe(upstream); upstream.pipe(client);
        });
        outbound.on('response', response => { response.resume(); client.destroy(); });
        outbound.on('error', () => client.destroy());
        outbound.end();
      });
      await new Promise((resolve, reject) => {
        const failed = () => reject(new Error('Browser relay could not bind its fixed loopback listener'));
        server.once('error', failed);
        server.listen({ host: '127.0.0.1', port: 18800, exclusive: true }, () => {
          server.off('error', failed);
          server.on('error', () => { for (const socket of [...clients, ...upstreams]) socket.destroy(); });
          resolve();
        });
      });
    },
    async stop() {
      for (const request of requests) request.destroy();
      for (const socket of [...clients, ...upstreams]) socket.destroy();
      requests.clear(); clients.clear(); upstreams.clear();
      const owned = server; server = undefined;
      if (owned) await new Promise(resolve => owned.close(resolve));
    },
  };
}
