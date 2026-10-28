#!/usr/bin/env python3
"""Probe deployed platform dependencies without inference or private-content logging."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile

METRIC_DIRECTORY = Path('/var/lib/homecompute-health')
EDGE_CA = '/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt'
UPSTREAM_KEYS = ('text', 'embedding', 'stt_danish', 'stt_multilingual', 'tts')
NAS_CONFIG = Path('/etc/homecompute/nas-recovery-backup.json')
NAS_STATE = Path('/var/lib/homecompute/nas-recovery-backup')
# Run at the existing consumer boundary. The host is intentionally denied direct
# access to the SSH-forward ports; do not weaken that firewall for monitoring.
UPSTREAM_PROBE = r'''
from concurrent.futures import ThreadPoolExecutor
import json, os
from pathlib import Path
import urllib.request, urllib.parse
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self, req, fp, code, msg, headers, newurl):
  return None
opener = urllib.request.build_opener(NoRedirect)
routes = {
 'text': ('COMPUTE_AUTOMATION_CANDIDATE_BASE_URL', '/health'),
 'embedding': ('EMBEDDING_BASE_URL', '/health'),
 'stt_danish': ('COMPUTE_STT_DANISH_BASE_URL', '/health'),
 'stt_multilingual': ('COMPUTE_STT_MULTILINGUAL_BASE_URL', '/health'),
 'tts': ('PLAPRE_TTS_BASE_URL', '/health/ready'),
}
try:
 key = Path('/run/secrets/compute_api_key').read_text().strip()
except OSError:
 key = ''
def check(item):
 name, (variable, suffix) = item
 try:
  value = urllib.parse.urlsplit(os.environ[variable])
  if value.scheme != 'http' or not value.hostname or value.username or value.password or value.query or value.fragment:
   return name, False
  url = urllib.parse.urlunsplit((value.scheme, value.netloc, suffix, '', ''))
  request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key} if key else {})
  with opener.open(request, timeout=5) as response:
   return name, response.status == 200
 except Exception:
  return name, False
with ThreadPoolExecutor(max_workers=5) as pool:
 print(json.dumps(dict(pool.map(check, routes.items())), sort_keys=True))
'''


def http_ready(url: str, *, resolve: str | None = None, ca: str | None = None) -> bool:
    command = ['curl', '--silent', '--fail', '--output', '/dev/null',
               '--connect-timeout', '2', '--max-time', '5',
               '--proto', '=https' if url.startswith('https:') else '=http']
    if resolve:
        command.extend(['--resolve', resolve])
    if ca:
        command.extend(['--cacert', ca])
    try:
        return subprocess.run([*command, url], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=7, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def upstream_readiness() -> dict[str, bool]:
    unavailable = dict.fromkeys(UPSTREAM_KEYS, False)
    try:
        result = subprocess.run(['docker', 'exec', '-i', 'homecompute-control-plane-litellm-1', 'python3', '-'],
                                input=UPSTREAM_PROBE, capture_output=True, text=True, timeout=9, check=False)
        if result.returncode != 0 or len(result.stdout) > 1024:
            return unavailable
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or set(value) != set(UPSTREAM_KEYS) or any(type(x) is not bool for x in value.values()):
            return unavailable
        return value
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired):
        return unavailable


def wyoming_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3) as connection:
            connection.settimeout(3)
            connection.sendall(b'{"type":"describe","version":"1.0.0"}\n')
            with connection.makefile('rb') as stream:
                line = stream.readline(65537)
            if len(line) > 65536 or not line.endswith(b'\n'):
                return False
            value = json.loads(line)
            return isinstance(value, dict) and value.get('type') == 'info'
    except (OSError, ValueError, RecursionError):
        return False


def private_record(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or not 0 < info.st_size <= 65536):
            raise ValueError('private bounded recovery receipt required')
        raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('recovery receipt exceeds its bound')
        value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('recovery receipt object required')
    return value


def nas_readiness(now: datetime | None = None) -> dict[str, bool]:
    deployment = NAS_STATE / 'deployment.json'
    if not any(path.exists() or path.is_symlink() for path in (NAS_CONFIG, deployment)):
        return {}
    unavailable = {'nas_backup_ready': False, 'nas_check_ready': False}
    try:
        if private_record(deployment).get('state') != 'scheduled':
            return unavailable
        permit = (NAS_STATE / 'backup-authorized').lstat()
        if (not stat.S_ISREG(permit.st_mode) or permit.st_uid != os.geteuid()
                or permit.st_mode & 0o077 or not 0 < permit.st_size <= 1024):
            return unavailable
        names = {f'homecompute-nas-recovery-{operation}.{kind}'
                 for operation in ('backup', 'check') for kind in ('timer', 'service')}
        result = subprocess.run(['systemctl', 'show', *sorted(names), '-p', 'Id', '-p', 'ActiveState', '-p', 'Result'],
                                capture_output=True, text=True, timeout=3, check=False)
        if result.returncode or len(result.stdout) > 8192:
            return unavailable
        units = {}
        for block in result.stdout.strip().split('\n\n'):
            fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            name = fields.get('Id')
            if name not in names or name in units:
                return unavailable
            units[name] = fields
        if set(units) != names:
            return unavailable
        current = now or datetime.now(timezone.utc)
        ready = {}
        for operation, maximum_age in (('backup', 36 * 3600), ('check', 8 * 86400)):
            receipt = private_record(NAS_STATE / f'last-{operation}.json')
            timestamp = datetime.fromisoformat(receipt['time'])
            age = (current - timestamp).total_seconds()
            evidence = (isinstance(receipt.get('snapshot'), str)
                        and re.fullmatch('[0-9a-f]{64}', receipt['snapshot']) is not None
                        if operation == 'backup' else receipt.get('passed') is True)
            timer = units[f'homecompute-nas-recovery-{operation}.timer']
            service = units[f'homecompute-nas-recovery-{operation}.service']
            ready[f'nas_{operation}_ready'] = bool(evidence and -120 <= age <= maximum_age
                and timer.get('ActiveState') == 'active' and service.get('Result') == 'success'
                and service.get('ActiveState') in {'inactive', 'active', 'activating'})
        return ready
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        return unavailable


def collect() -> dict[str, bool]:
    jobs = {
        'edge_process_healthy': lambda: http_ready(
            'https://home-core.tail479ad.ts.net:8443/healthz',
            resolve='home-core.tail479ad.ts.net:8443:100.110.248.102'),
        'n8n_ready': lambda: http_ready('http://127.0.0.1:15678/healthz/readiness'),
        'n8n_edge_ready': lambda: http_ready('https://n8n.home.arpa:8443/healthz/readiness',
                                           resolve='n8n.home.arpa:8443:100.110.248.102', ca=EDGE_CA),
        'wyoming_tts_ready': lambda: wyoming_ready('172.28.200.4', 10201),
        'wyoming_stt_ready': lambda: wyoming_ready('172.28.200.5', 10301),
        'upstreams': upstream_readiness,
        'nas': nas_readiness,
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        result = {name: future.result() for name, future in futures.items()}
    upstreams = result.pop('upstreams')
    result.update(result.pop('nas'))
    result.update({name + '_ready': state for name, state in upstreams.items()})
    result['platform_ready'] = all(result.values())
    return result


def render(value: dict[str, bool]) -> str:
    result = []
    for name, state in sorted(value.items()):
        key = 'homecompute_' + name
        result.extend([f'# HELP {key} Deployed consumer-path readiness; no generation is performed.',
                       f'# TYPE {key} gauge', f'{key} {int(state)}'])
    # Preserve existing dashboard names while measuring the actual placement.
    legacy = {'gx10_model_ready': 'text_ready', 'gx10_text_ready': 'text_ready',
              'gx10_embedding_ready': 'embedding_ready', 'gx10_stt_ready': 'stt_danish_ready',
              'gx10_tts_ready': 'tts_ready', 'gx10_wyoming_ready': 'wyoming_tts_ready'}
    for old, new in legacy.items():
        result.append(f'homecompute_{old} {int(value[new])}')
    return '\n'.join(result) + '\n'


def publish(value: dict[str, bool], directory: Path = METRIC_DIRECTORY) -> None:
    with tempfile.NamedTemporaryFile(mode='w', prefix='.homecompute-health.', dir=directory, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(render(value))
            output.flush()
            os.fsync(output.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, directory / 'homecompute-health.prom')
        finally:
            temporary.unlink(missing_ok=True)


def main() -> int:
    value = collect()
    publish(value)
    return 0 if value['platform_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
