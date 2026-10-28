#!/usr/bin/env python3
"""Stage one fixed native MCP HTTPS relay; start/stop never affect other services."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any

INSTALL = Path('/opt/homecompute/native-capabilities')
TLS = Path('/srv/state/native-capabilities/tls')
CA = Path('/srv/state/control-plane/caddy-data/caddy/pki/authorities/local')
TOKENS = Path('/var/lib/homecompute-openclaw/config/native-mcp-tokens.json')
UNIT = Path('/etc/systemd/system.attached/homecompute-native-mcp-relay.service')
NAME = 'homecompute-native-mcp-relay'
HOST = 'homecompute-capabilities.home.arpa'
IMAGE = 'caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f'
BIN = Path('/run/current-system/sw/bin')
OPENSSL_FALLBACK = Path('/nix/store/gqv7phqvmll7dprdmya6102ph64wvj0h-openssl-3.6.3-bin/bin/openssl')
ROOT_UID, TLS_UID, TLS_GID = 0, 1000, 1000
RULE = ['INPUT', '-i', 'br-hc-agents', '-s', '10.77.20.2/32', '-d', '10.77.20.1/32',
        '-p', 'tcp', '--dport', '19445', '-m', 'conntrack', '--ctstate', 'NEW,ESTABLISHED',
        '-m', 'comment', '--comment', 'hc-native-mcp-relay', '-j', 'ACCEPT']


def guard() -> None:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core' or sys.platform != 'linux':
        raise ValueError('root on Linux home-core required')


def call(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=30, env={'PATH': str(BIN), 'LANG': 'C'})
    if check and result.returncode:
        raise ValueError('fixed relay operation failed; reconcile private state')
    return result


def openssl_binary() -> str:
    preferred = BIN / 'openssl'
    return str(preferred if preferred.is_file() else OPENSSL_FALLBACK)


def directory(path: Path, mode: int = 0o755, *, apply_mode: bool = True) -> None:
    if path.is_symlink():
        raise ValueError('managed directories cannot be symlinks')
    if not path.exists():
        directory(path.parent, apply_mode=False)
        path.mkdir(mode=mode)
        os.chown(path, ROOT_UID, 0)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o022:
        raise ValueError('managed parent must be root-owned and not writable by others')
    if apply_mode:
        os.chmod(path, mode)


def regular(path: Path, owner: int | None = None, mode: int | None = None) -> None:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022
            or owner is not None and info.st_uid != owner
            or mode is not None and stat.S_IMODE(info.st_mode) != mode):
        raise ValueError('unsafe managed file')


def write(path: Path, data: bytes, owner: int | None = None, group: int = 0, mode: int = 0o644) -> None:
    owner = ROOT_UID if owner is None else owner
    if path.exists() or path.is_symlink():
        regular(path, owner)
        if path.read_bytes() == data and stat.S_IMODE(path.stat().st_mode) == mode:
            return
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode); os.fchown(stream.fileno(), owner, group)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def caddyfile() -> str:
    return f'''{{
    admin off
    auto_https off
    log {{
        output discard
    }}
}}
https://{HOST}:19445 {{
    bind 10.77.20.1
    tls /run/tls/server.crt /run/tls/server.key
    request_body {{
        max_size 10MB
    }}
    @mcp {{
        remote_ip 10.77.20.2
        method POST
        path /mcp
        expression {{http.request.orig_uri}} == "/mcp"
    }}
    handle @mcp {{
        request_body {{
            max_size 128KB
        }}
        reverse_proxy 127.0.0.1:18793 {{
            transport http {{
                dial_timeout 3s
                response_header_timeout 15s
            }}
        }}
    }}
    @embeddings {{
        remote_ip 10.77.20.2
        method POST
        path /v1/embeddings
        expression {{http.request.orig_uri}} == "/v1/embeddings"
    }}
    handle @embeddings {{
        request_body {{
            max_size 128KB
        }}
        reverse_proxy 127.0.0.1:18793 {{
            transport http {{
                dial_timeout 3s
                response_header_timeout 60s
            }}
        }}
    }}
    @speech {{
        remote_ip 10.77.20.2
        method POST
        path /v1/audio/transcriptions /v1/audio/speech
        expression {{http.request.orig_uri}} in ["/v1/audio/transcriptions", "/v1/audio/speech"]
    }}
    handle @speech {{
        reverse_proxy 127.0.0.1:18793 {{
            transport http {{
                dial_timeout 3s
                response_header_timeout 120s
            }}
        }}
    }}
    respond 404
}}
'''


def container_command() -> list[str]:
    return [str(BIN / 'docker'), 'run', '--rm', '--pull=never', '--name', NAME,
            '--label', 'homecompute.native-mcp-relay=true', '--network=host', '--user', '1000:1000',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=0.25',
            '--memory=128m', '--pids-limit=32', '--log-driver=local', '--log-opt=max-size=1m', '--log-opt=max-file=2',
            '--mount', f'type=bind,src={INSTALL}/Caddyfile,dst=/etc/caddy/Caddyfile,readonly',
            '--mount', f'type=bind,src={TLS}/server.crt,dst=/run/tls/server.crt,readonly',
            '--mount', f'type=bind,src={TLS}/server.key,dst=/run/tls/server.key,readonly',
            '--tmpfs', '/runtime:rw,nosuid,nodev,exec,size=96m,uid=1000,gid=1000,mode=0700',
            '--tmpfs', '/data:rw,nosuid,nodev,noexec,size=8m,uid=1000,gid=1000,mode=0700',
            '--tmpfs', '/config:rw,nosuid,nodev,noexec,size=8m,uid=1000,gid=1000,mode=0700',
            '--entrypoint', '/bin/sh', IMAGE, '-ec',
            'cp /usr/bin/caddy /runtime/caddy; exec /runtime/caddy run --config /etc/caddy/Caddyfile --adapter caddyfile']


def firewall(enable: bool) -> None:
    guard()
    command = [str(BIN / 'iptables'), '-w']
    exists = call(command + ['-C', *RULE], check=False).returncode == 0
    if enable:
        if not exists:
            call(command + ['-I', RULE[0], '1', *RULE[1:]])
    else:
        for _ in range(32):
            if not exists:
                return
            call(command + ['-D', *RULE])
            exists = call(command + ['-C', *RULE], check=False).returncode == 0
        raise ValueError('too many duplicate managed firewall grants')


def unit_text() -> str:
    script = INSTALL / 'setup-native-capabilities.py'
    return f'''[Unit]
Description=HomeCompute private native MCP HTTPS relay
After=homecompute-agents-network.service docker.service homecompute-openclaw-adapter.service
Requires=homecompute-agents-network.service docker.service
[Service]
Type=simple
User=root
ExecStart={BIN}/python3 {script} run
ExecStartPre=+{BIN}/python3 {script} check
ExecStop={BIN}/python3 {script} stop
ExecStopPost={BIN}/python3 {script} close-firewall
Restart=on-failure
RestartSec=10
TimeoutStopSec=20
UMask=0077
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
PrivateTmp=yes
ReadOnlyPaths={INSTALL} {TLS}
RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK
CapabilityBoundingSet=CAP_NET_ADMIN
[Install]
WantedBy=multi-user.target
'''


def certificate() -> bool:
    for path in (CA / 'root.crt', CA / 'root.key'):
        regular(path)
    if (CA / 'root.key').stat().st_mode & 0o077:
        raise ValueError('CA signing key must remain private')
    cert, key = TLS / 'server.crt', TLS / 'server.key'
    if cert.exists() or key.exists() or cert.is_symlink() or key.is_symlink():
        regular(cert, TLS_UID, 0o400); regular(key, TLS_UID, 0o400)
        call([openssl_binary(), 'verify', '-CAfile', str(CA / 'root.crt'), '-verify_hostname', HOST, str(cert)])
        call([openssl_binary(), 'x509', '-in', str(cert), '-checkend', '604800', '-noout'])
        return False
    with tempfile.TemporaryDirectory(prefix='.issue-', dir=TLS) as temporary:
        tmp = Path(temporary)
        extension = tmp / 'extensions'
        extension.write_text(f'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:{HOST}\n')
        openssl = openssl_binary()
        call([openssl, 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', str(tmp / 'key')])
        call([openssl, 'req', '-new', '-key', str(tmp / 'key'), '-subj', '/CN=' + HOST, '-out', str(tmp / 'request')])
        call([openssl, 'x509', '-req', '-in', str(tmp / 'request'), '-CA', str(CA / 'root.crt'), '-CAkey', str(CA / 'root.key'),
              '-set_serial', '0x' + secrets.token_hex(16), '-days', '90', '-sha256', '-extfile', str(extension), '-out', str(tmp / 'cert')])
        call([openssl, 'verify', '-CAfile', str(CA / 'root.crt'), '-verify_hostname', HOST, str(tmp / 'cert')])
        write(key, (tmp / 'key').read_bytes(), TLS_UID, TLS_GID, 0o400)
        write(cert, (tmp / 'cert').read_bytes(), TLS_UID, TLS_GID, 0o400)
    return True


def native_tokens() -> bool:
    user = pwd.getpwnam('homecompute-openclaw')
    if TOKENS.exists() or TOKENS.is_symlink():
        regular(TOKENS, user.pw_uid, 0o600)
        if TOKENS.stat().st_size > 16384:
            raise ValueError('native credential file exceeds budget')
        value = json.loads(TOKENS.read_bytes())
        if (not isinstance(value, dict) or set(value) not in ({'openclaw'}, {'openclaw', 'hermes'})
                or any(not isinstance(token, str) or len(token) < 32 or '\n' in token or '\r' in token
                       or token.startswith(('replace', 'openshell:resolve:')) for token in value.values())
                or len(set(value.values())) != len(value)):
            raise ValueError('independent OpenClaw credential required')
        if 'hermes' in value:
            write(TOKENS, json.dumps({'openclaw': value['openclaw']}).encode(), user.pw_uid, user.pw_gid, 0o600)
            return True
        return False
    # Existing adapter-owned config directory is never recreated or rechowned.
    info = TOKENS.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != user.pw_uid or info.st_mode & 0o077:
        raise ValueError('existing private adapter configuration directory required')
    value = {'openclaw': secrets.token_urlsafe(48)}
    write(TOKENS, json.dumps(value).encode(), user.pw_uid, user.pw_gid, 0o600)
    return True


def install() -> dict[str, Any]:
    guard()
    call([str(BIN / 'docker'), 'image', 'inspect', IMAGE])
    for path, mode in ((INSTALL, 0o755), (TLS.parent, 0o750), (TLS, 0o750), (UNIT.parent, 0o755)):
        directory(path, mode)
    lock = INSTALL / '.install.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        regular(lock, ROOT_UID, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        issued = certificate()
        created = native_tokens()
        write(INSTALL / 'Caddyfile', caddyfile().encode())
        write(INSTALL / 'setup-native-capabilities.py', Path(__file__).read_bytes())
        write(UNIT, unit_text().encode())
    finally:
        os.close(descriptor)
    # Stage only: no firewall grant, container start, enable or unrelated reload.
    return {'installed': True, 'service': UNIT.name, 'tls_issued': issued,
            'native_credentials_created': created, 'endpoint': f'https://{HOST}:19445/mcp', 'started': False}


def run() -> int:
    check(verify_tls=False)  # ExecStartPre validates TLS with full root authority.
    try:
        firewall(True)
        return subprocess.run(container_command(), env={'PATH': str(BIN), 'LANG': 'C'}).returncode
    finally:
        firewall(False)


def check(verify_tls: bool = True) -> None:
    guard()
    regular(INSTALL / 'Caddyfile', ROOT_UID, 0o644)
    if (INSTALL / 'Caddyfile').read_text() != caddyfile():
        raise ValueError('fixed relay configuration required')
    regular(TLS / 'server.crt', TLS_UID, 0o400); regular(TLS / 'server.key', TLS_UID, 0o400)
    if verify_tls:
        call([openssl_binary(), 'verify', '-CAfile', str(CA / 'root.crt'), '-verify_hostname', HOST, str(TLS / 'server.crt')])
        call([openssl_binary(), 'x509', '-in', str(TLS / 'server.crt'), '-checkend', '0', '-noout'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'check', 'run', 'stop', 'close-firewall'))
    args = parser.parse_args()
    try:
        guard()
        if args.command == 'install':
            print(json.dumps(install()))
        elif args.command == 'run':
            return run()
        elif args.command == 'check':
            check()
        elif args.command == 'stop':
            call([str(BIN / 'docker'), 'stop', '--time=10', NAME], check=False)
        else:
            firewall(False)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Native relay operation refused; reconcile private configuration.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
