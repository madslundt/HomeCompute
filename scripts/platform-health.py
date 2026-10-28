#!/usr/bin/env python3
"""Probe deployed platform dependencies without inference or private-content logging."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

METRIC_DIRECTORY = Path('/var/lib/homecompute-health')
EDGE_CA = '/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt'
UPSTREAM_KEYS = ('text', 'embedding', 'stt_danish', 'stt_multilingual', 'tts')
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
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        result = {name: future.result() for name, future in futures.items()}
    upstreams = result.pop('upstreams')
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
