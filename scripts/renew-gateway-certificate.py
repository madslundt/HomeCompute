#!/usr/bin/env python3
"""Renew the private gateway's publicly trusted Tailscale hostname certificate."""
import argparse
import hashlib
import fcntl
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time


DOMAIN = 'home-core.tail479ad.ts.net'
DIRECTORY = Path('/srv/state/control-plane/caddy-data/tailscale-tls')
CONTAINER = 'homecompute-control-plane-caddy-1'


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Gateway certificate operation failed: ' + command[0])
    return result.stdout


def validate_pair(cert, key):
    # A local TLS handshake validates the public chain, hostname and key match
    # using stdlib SSL, without depending on a host OpenSSL command installation.
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')
    server_socket, client_socket = socket.socketpair()
    server_socket.settimeout(10)
    client_socket.settimeout(10)
    def serve():
        try:
            with server_context.wrap_socket(server_socket, server_side=True):
                pass
        except OSError:
            server_socket.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        with client_context.wrap_socket(client_socket, server_hostname=DOMAIN) as connection:
            expiry = ssl.cert_time_to_seconds(connection.getpeercert()['notAfter'])
            if expiry - time.time() < 14 * 86400:
                raise RuntimeError('Gateway certificate has insufficient remaining validity.')
    finally:
        client_socket.close()
        thread.join(timeout=15)


def publish_pair(directory, certificate, key):
    """Publish a complete version before atomically switching the pair pointer."""
    version = hashlib.sha256(certificate).hexdigest()
    current = directory / 'current'
    if current.is_symlink() and os.readlink(current) == version:
        if (current / 'cert.pem').read_bytes() != certificate or (current / 'key.pem').read_bytes() != key:
            raise RuntimeError('Published gateway certificate state is inconsistent.')
        return False
    destination = directory / version
    destination.mkdir(mode=0o700, exist_ok=True)
    for name, content, mode in [('cert.pem', certificate, 0o644), ('key.pem', key, 0o600)]:
        target = destination / name
        if target.exists():
            if target.is_symlink() or target.read_bytes() != content:
                raise RuntimeError('Existing gateway certificate version does not match.')
        else:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        target.chmod(mode)
    with tempfile.TemporaryDirectory(prefix='.publish-', dir=directory) as temporary:
        link = Path(temporary) / 'current'
        link.symlink_to(version)
        os.replace(link, current)
    return True


def renew(no_restart=False):
    with tempfile.TemporaryDirectory(prefix='.issue-', dir=DIRECTORY) as temporary:
        cert, key = Path(temporary) / 'cert.pem', Path(temporary) / 'key.pem'
        run(['tailscale', 'cert', '--min-validity=720h', '--cert-file=' + str(cert),
             '--key-file=' + str(key), DOMAIN])
        validate_pair(cert, key)
        changed = publish_pair(DIRECTORY, cert.read_bytes(), key.read_bytes())
    pending = DIRECTORY / 'pending-restart'
    if changed:
        pending.touch(mode=0o600)
    if not no_restart and pending.exists():
        run(['docker', 'restart', CONTAINER])
        pending.unlink()
        print('Renewed gateway certificate and restarted Caddy.')
    else:
        print('Gateway public certificate is valid; ' + ('provisioned.' if changed else 'unchanged.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-restart', action='store_true', help='Provision before initial Caddy startup.')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise SystemExit('Run as root on home-core.')
    os.umask(0o077)
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    if DIRECTORY.is_symlink() or DIRECTORY.stat().st_uid != 0:
        raise SystemExit('Gateway certificate directory must be owned by root and not be a symlink.')
    DIRECTORY.chmod(0o700)
    fd = os.open(DIRECTORY / 'renewal.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        renew(args.no_restart)


if __name__ == '__main__':
    main()
