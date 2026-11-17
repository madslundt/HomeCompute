#!/usr/bin/env python3
"""Retire only the OpenClaw dashboard and Agent Office front doors on Home Core."""
import argparse
from contextlib import ExitStack
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import time

from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'office_ingress', ROOT / 'scripts/setup-agent-office-ingress.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
STATE = Path('/var/lib/homecompute-agent-office')
CONTROL = Path('/etc/systemd/system.control')
NAMES = (core.CADDY, core.HOMEPAGE)
UNITS = (
    'homecompute-openclaw-dashboard-serve.service',
    'homecompute-openclaw-dashboard-proxy.service',
    'homecompute-agent-office-proxy.service',
    'homecompute-agent-office-firewall.service',
)
OFFICE_SITE = re.compile(r'^agents\.home\.arpa \{\n.*?^\}\n', re.M | re.S)
NATIVE_SITE = re.compile(
    r'^(?:# Native OpenClaw dashboard[^\n]*\n# OpenClaw[^\n]*\n)?'
    r'192\.168\.30\.122 \{\n\ttls internal\n\tencode zstd gzip\n'
    r'\treverse_proxy 172\.28\.200\.1:18792\n\}\n\n?', re.M)
OFFICE_HANDLE = (
    '\t\thandle {\n'
    '\t\t\treverse_proxy 172.28.200.1:18802 {\n'
    '\t\t\t\theader_up Host 127.0.0.1:18802\n'
    '\t\t\t\theader_up X-HomeCompute-Office-Origin https://agents.home.arpa\n'
    '\t\t\t\ttransport http {\n'
    '\t\t\t\t\tdial_timeout 3s\n'
    '\t\t\t\t\tresponse_header_timeout 10s\n'
    '\t\t\t\t}\n'
    '\t\t\t}\n'
    '\t\t}\n'
)
CARDS = {'Agent office': 'homecore-agent-office', 'OpenClaw': 'homecore-openclaw'}


def patch(caddy, homepage, source_caddy, source_homepage):
    canonical = OFFICE_SITE.findall(source_caddy)
    active = OFFICE_SITE.findall(caddy)
    if len(canonical) != 1 or len(active) != 1 or 'handle_path /hermes/*' not in active[0]:
        raise ValueError('expected dedicated Hermes route')
    retired = '\t\thandle {\n\t\t\trespond "Not found" 404\n\t\t}\n'
    if canonical[0].count(retired) != 1:
        raise ValueError('expected canonical retirement response')
    site = active[0].replace(OFFICE_HANDLE, retired)
    if site.count(retired) != 1:
        raise ValueError('unrecognized Agent Office route')
    site = site.replace(
        '\t\t# The observer still validates Origin and requires its own password.\n'
        '\t\t# Its native listener and both relay hops remain on private interfaces.\n', '')
    site = site.replace(
        '# Return only those chunks to Hermes; Office keeps its own assets/API.',
        '# Return only those chunks to Hermes.')
    caddy = NATIVE_SITE.sub('', OFFICE_SITE.sub(lambda _: site, caddy))
    if '172.28.200.1:18792' in caddy or '172.28.200.1:18802' in caddy:
        raise ValueError('unrecognized legacy dashboard proxy')
    for name, service_id in CARDS.items():
        card = re.compile(
            rf'^    - {re.escape(name)}:\n        id: {service_id}\n'
            r'(?:^        [^\n]+\n)*\n?', re.M)
        if service_id in source_homepage or len(card.findall(homepage)) > 1:
            raise ValueError('unexpected Homepage retirement source')
        homepage = card.sub('', homepage)
        if service_id in homepage or f'    - {name}:' in homepage:
            raise ValueError('unrecognized legacy Homepage card')
    return caddy, homepage


def config_path(row, destination, relative):
    path = core.mount(row, destination)
    if destination == '/app/config':
        path /= 'services.yaml'
    pattern = '/srv/homecompute/releases/[a-f0-9]{40}/' + re.escape(relative)
    if not re.fullmatch(pattern, str(path)) or path.resolve() != path:
        raise ValueError('expected canonical release config mount')
    return path


def unit_state():
    result = {}
    for name in UNITS:
        props = ('FragmentPath', 'ActiveState', 'DropInPaths')
        args = ['systemctl', 'show', name, *[f'--property={p}' for p in props]]
        values = dict(line.split('=', 1) for line in core.call(args).splitlines())
        if values['DropInPaths'] or values['ActiveState'] not in ('active', 'inactive'):
            raise ValueError('unexpected dashboard unit state')
        path = CONTROL / name
        if path.is_symlink():
            if path.readlink() != Path('/dev/null'):
                raise ValueError('unexpected dashboard unit control link')
            control = 'masked'
        elif path.exists():
            control = core.sha(core.protected_file(path))
            if values['FragmentPath'] != str(path):
                raise ValueError('unexpected dashboard unit control file')
        else:
            control = None
        fragment = values['FragmentPath']
        if control == 'masked' and fragment == '/dev/null':
            fragment = ''
        if fragment and fragment not in (str(path), f'/etc/systemd/system/{name}',
                                          f'/etc/systemd/system.attached/{name}'):
            raise ValueError('unexpected dashboard unit fragment')
        result[name] = {'active': values['ActiveState'] == 'active', 'control': control,
                        'unit': core.sha(Path(fragment).read_bytes()) if fragment else None}
    return result


def verify_homepage():
    request = Request('http://127.0.0.1/api/services', headers={'Host': 'home-core'})
    with urlopen(request, timeout=4) as response:
        groups = json.load(response)
    cards = {service['id'] for group in groups for service in group['services']}
    if not {'homecore-hermes', 'homecore-openbot'} <= cards or cards.intersection(CARDS.values()):
        raise ValueError('Homepage retirement verification failed')


def plan(revision):
    rows = [core.inspect(name) for name in NAMES]
    paths = [
        config_path(rows[0], '/etc/caddy/Caddyfile', 'deploy/control-plane/Caddyfile'),
        config_path(rows[1], '/app/config', 'deploy/homepage/config/services.yaml'),
    ]
    if any(m['Destination'] == '/app/config/services.yaml' for m in rows[1]['Mounts']):
        raise ValueError('Homepage has an explicit config override')
    before = [core.protected_file(path) for path in paths]
    after = [value.encode() for value in patch(
        *(value.decode() for value in before),
        (ROOT / 'deploy/control-plane/Caddyfile').read_text(),
        (ROOT / 'deploy/homepage/config/services.yaml').read_text(),
    )]
    serve = json.loads(core.call(['tailscale', 'serve', 'status', '--json']))
    routes = {name: site for name, site in serve.get('Web', {}).items()
              if name.endswith(':18791')}
    if routes and routes != {'home-core.tail479ad.ts.net:18791': {
            'Handlers': {'/': {'Proxy': 'http://10.77.20.2:18891'}}}}:
        raise ValueError('port 18791 has a different owner')
    units = unit_state()
    tcp = serve.get('TCP', {}).get('18791')
    if any(name.endswith(':18791') and enabled
           for name, enabled in serve.get('AllowFunnel', {}).items()):
        raise ValueError('port 18791 has Funnel enabled')
    if (tcp or routes) and (tcp != {'HTTPS': True} or not routes
                            or not units[UNITS[0]]['active']):
        raise ValueError('port 18791 is not managed by the dashboard service')
    report = {
        'revision': revision, 'paths': list(map(str, paths)),
        'containers': [row['Id'] for row in rows],
        'before': list(map(core.sha, before)), 'after': list(map(core.sha, after)),
        'units': units, 'serve': routes, 'maintenance': core.maintenance_evidence(),
    }
    return report, paths, before, after


def apply(report, paths, before, after):
    core.call(['docker', 'exec', '-i', core.CADDY, 'caddy', 'adapt', '--config',
               '-', '--adapter', 'caddyfile', '--validate'], data=after[0].decode())
    if plan(report['revision'])[0] != report:
        raise ValueError('dashboard retirement baseline changed')
    backup = STATE / f'retirement-{time.time_ns()}'
    backup.mkdir(mode=0o700, parents=True)
    receipt = backup / 'receipt.json'
    receipt.write_text(json.dumps(report, indent=2) + '\n')
    for relative, data in zip(('Caddyfile', 'services.yaml'), before):
        (backup / relative).write_bytes(data)
    CONTROL.mkdir(exist_ok=True)
    changed, masked = [], []
    try:
        for name in UNITS:
            if report['units'][name]['active']:
                core.call(['systemctl', 'stop', name])
            path = CONTROL / name
            if report['units'][name]['control'] != 'masked':
                if path.exists():
                    (backup / name).write_bytes(core.protected_file(path))
                    path.unlink()
                masked.append(name)
                path.symlink_to('/dev/null')
        core.call(['systemctl', 'daemon-reload'])
        for name, path, old, new in zip(NAMES, paths, before, after):
            if old != new:
                core.in_place(path, old, new)
                changed.append((name, path, old, new))
                core.call(['docker', 'restart', name], timeout=90)
        for attempt in range(15):
            try:
                verify_homepage()
                break
            except OSError:
                if attempt == 14:
                    raise
                time.sleep(1)
        if any(unit['active'] or unit['control'] != 'masked' for unit in unit_state().values()):
            raise ValueError('retired dashboard units are still available')
        serve = json.loads(core.call(['tailscale', 'serve', 'status', '--json']))
        if '18791' in serve.get('TCP', {}):
            raise ValueError('retired dashboard tailnet port is still present')
        if report['containers'] != [core.inspect(name)['Id'] for name in NAMES]:
            raise ValueError('dashboard rollout unexpectedly replaced a container')
    except Exception:
        for name in masked:
            path = CONTROL / name
            if path.is_symlink() and path.readlink() == Path('/dev/null'):
                path.unlink()
            original = backup / name
            if original.exists():
                path.write_bytes(original.read_bytes())
        core.call(['systemctl', 'daemon-reload'])
        for name in reversed(UNITS):
            if report['units'][name]['active']:
                core.call(['systemctl', 'start', name])
        for name, path, old, new in reversed(changed):
            core.in_place(path, new, old)
            core.call(['docker', 'restart', name], timeout=90)
        raise
    receipt.write_text(json.dumps({**report, 'applied': True}, indent=2) + '\n')
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply-plan-sha256')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise ValueError('run on Home Core as root')
    core.release(args.revision)
    with ExitStack() as stack:
        for name in ('/var/lib/homecompute/deploy.lock',
                     '/var/lib/homecompute-docker-maintenance/operation.lock'):
            lock = stack.enter_context(open(name, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report, paths, before, after = plan(args.revision)
        digest = core.sha(json.dumps(report, sort_keys=True).encode())
        if not args.apply_plan_sha256:
            print(json.dumps({**report, 'plan_sha256': digest}))
            return
        if args.apply_plan_sha256 != digest:
            raise ValueError('reviewed dashboard retirement plan required')
        print(apply(report, paths, before, after))


if __name__ == '__main__':
    main()
