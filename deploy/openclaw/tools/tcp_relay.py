"""Fixed CDP byte relay; Chromium remains on its internal-only network."""
import os
import select
import socket
import socketserver
import threading

LIMIT = threading.BoundedSemaphore(16)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not LIMIT.acquire(blocking=False):
            return
        try:
            host, port = os.environ['BROWSER_UPSTREAM'].rsplit(':', 1)
            with socket.create_connection((host, int(port)), timeout=5) as upstream:
                for endpoint in [self.request, upstream]:endpoint.settimeout(15)
                while True:
                    ready, _, _ = select.select([self.request, upstream], [], [], 120)
                    if not ready:return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:return
                        (upstream if source is self.request else self.request).sendall(data)
        except OSError:
            return
        finally:
            LIMIT.release()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    Server(('0.0.0.0', 18800), Handler).serve_forever()
