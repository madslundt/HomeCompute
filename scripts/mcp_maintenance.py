#!/usr/bin/env python3
"""Root-only, immutable MCP candidates. The controller owns approval and deploy lock."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

from infrastructure_maintenance import (ENV, MAX_OUTPUT, bounded_run, canonical,
    identifier, root_json, trusted_path, validate_plan)
from infrastructure_update_policy import SEMVER, classify_version

COMMAND = '/usr/local/libexec/homecompute-mcp-maintenance'
STATE = Path('/var/lib/homecompute-mcp-maintenance')
SEED = Path('/opt/homecompute/mcp-maintenance/automation')
ENV_FILE = Path('/etc/homecompute/automation.env')
DOCKER = '/run/current-system/sw/bin/docker'
TAR = '/run/current-system/sw/bin/tar'
COMPONENTS = {'aula-mcp': ('Casperjuel/aula-mcp', 'aula.Dockerfile', 7878),
              'tilbudstrolden-mcp': ('olgasafonova/tilbudstrolden-mcp', 'tilbudstrolden.Dockerfile', 8000)}
IMAGE = re.compile(r'sha256:[a-f0-9]{64}')


def output(value):
    return (canonical(value) + '\n').encode()


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value, *, raw=False):
    trusted_path(Path(path).parent, 0, directory=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(value if raw else output(value))
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def selected(component):
    if not isinstance(component, str) or component not in COMPONENTS:
        raise ValueError('fixed MCP component required')
    return COMPONENTS[component]


def upstream(component):
    repo = selected(component)[0]
    def fetch(url):
        request = urllib.request.Request(url, headers={'User-Agent': 'HomeCompute-MCP-maintenance'})
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.url != url:
                raise ValueError('upstream redirect requires review')
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('upstream metadata exceeds budget')
            return json.loads(raw)
    release = fetch('https://api.github.com/repos/' + repo + '/releases/latest')
    tag = release.get('tag_name')
    if (not isinstance(tag, str) or not SEMVER.fullmatch(tag)
            or release.get('draft') is not False or release.get('prerelease') is not False):
        raise ValueError('published stable upstream release required')
    commit = fetch('https://api.github.com/repos/' + repo + '/commits/' + tag).get('sha')
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('immutable official upstream commit required')
    package = fetch('https://raw.githubusercontent.com/' + repo + '/' + commit + '/package.json')
    version = package.get('version')
    if not isinstance(version, str) or not SEMVER.fullmatch(version) or version.removeprefix('v') != tag.removeprefix('v'):
        raise ValueError('upstream unstable release requires review')
    return commit, version


def recipe(text, commit):
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('immutable source commit required')
    value, count = re.subn(r'(?<=git fetch --depth 1 origin )[a-f0-9]{40}(?= \\\n)', commit, text)
    if count != 1 or 'git apply' not in text or '--check' not in text:
        raise ValueError('reviewed pinned source and mandatory patch checks required')
    return value


def invariants(row):
    config = dict(row['Config'])
    for key in ('Image', 'Hostname'):
        config.pop(key, None)
    config['Labels'] = {k: v for k, v in (config.get('Labels') or {}).items()
                        if not k.startswith('com.docker.compose.')}
    networks = {name: {key: data.get(key) for key in ('IPAMConfig', 'Aliases', 'IPPrefixLen', 'IPAddress')}
                for name, data in row['NetworkSettings']['Networks'].items()}
    mounts = sorted(({key: value for key, value in mount.items() if key != 'Name'}
                     for mount in row['Mounts']), key=canonical)
    return {'config': config, 'host': row['HostConfig'], 'mounts': mounts, 'networks': networks}


# Only health, initialize, and tools/list: no tool invocation or credential output.
MCP_PROBE = r'''(async()=>{
const url=process.argv[1], headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'};
const health=await fetch(url.replace('/mcp','/healthz'),{signal:AbortSignal.timeout(10000)});if(!health.ok)throw Error('health');
async function call(body){const r=await fetch(url,{method:'POST',headers,body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
 if(!r.ok)throw Error('http');const session=r.headers.get('mcp-session-id');if(session)headers['Mcp-Session-Id']=session;
 if(r.status===202)return null;const text=await r.text();if(text.length>524288)throw Error('budget');
 if(r.headers.get('content-type')?.includes('application/json'))return JSON.parse(text);
 const data=text.split('\n').filter(x=>x.startsWith('data:')).map(x=>JSON.parse(x.slice(5)));return data.find(x=>x.id===body.id);}
try{const init=await call({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'homecompute-maintenance',version:'1'}}});
 if(!init?.result?.protocolVersion||init.error)throw Error('initialize');headers['MCP-Protocol-Version']=init.result.protocolVersion;
 await call({jsonrpc:'2.0',method:'notifications/initialized'});
 const tools=await call({jsonrpc:'2.0',id:2,method:'tools/list',params:{}});if(!Array.isArray(tools?.result?.tools)||!tools.result.tools.length||tools.error)throw Error('tools');
 console.log('mcp-ok');}finally{if(headers['Mcp-Session-Id'])await fetch(url,{method:'DELETE',headers,signal:AbortSignal.timeout(5000)}).catch(()=>{});}
})().catch(()=>process.exit(1));'''


class Maintenance:
    def __init__(self, *, runner=bounded_run, state=STATE, seed=SEED, clock=time.time):
        if os.geteuid() != 0:
            raise ValueError('root maintenance required')
        self.runner, self.state, self.seed, self.clock = runner, Path(state), Path(seed), clock
        trusted_path(self.state.parent, 0, directory=True)
        self.state.mkdir(mode=0o700, exist_ok=True)
        trusted_path(self.state, 0, directory=True)
        if self.state.stat().st_mode & 0o077:
            raise ValueError('private MCP candidate state required')

    def run(self, argv, timeout=30):
        result = self.runner(argv, timeout=timeout, env=ENV.copy(), cwd='/', shell=False)
        if (result.returncode or not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes)
                or len(result.stdout) + len(result.stderr) > MAX_OUTPUT):
            raise ValueError('bounded MCP maintenance command failed')
        return result.stdout

    def inspect(self, component):
        selected(component)
        rows = json.loads(self.run([DOCKER, 'inspect', 'homecompute-automation-' + component + '-1']))
        if (not isinstance(rows, list) or len(rows) != 1 or not IMAGE.fullmatch(rows[0].get('Image', ''))
                or not re.fullmatch('[a-f0-9]{64}', rows[0].get('Id', ''))):
            raise ValueError('exact existing MCP container required')
        return rows[0]

    def compose(self, directory, rollback=False, *, filename=None):
        return [DOCKER, 'compose', '--project-name', 'homecompute-automation', '--project-directory', str(directory),
                '--env-file', str(ENV_FILE), '-f', str(directory / 'compose.yaml'),
                '-f', str(directory / (filename or ('rollback.yaml' if rollback else 'production.yaml')))]

    def config_hash(self, directory, component):
        raw = self.run(self.compose(directory, filename='baseline.yaml') + ['config', '--hash', component]).decode().strip().split()
        if len(raw) != 2 or raw[0] != component or not re.fullmatch('[a-f0-9]{64}', raw[1]):
            raise ValueError('exact rendered MCP service configuration hash required')
        return raw[1]

    def validate_resources(self, directory, component, baseline):
        """Compose labels survive docker update; bind its independently mutable fields."""
        service = json.loads(self.run(self.compose(directory) + ['config', '--format', 'json']))['services'][component]
        memory = int(service.get('mem_limit', 0))
        restart = service.get('restart', 'no').split(':')
        expected = {'NanoCpus': int(float(service.get('cpus', 0)) * 1_000_000_000),
                    'Memory': memory, 'MemorySwap': int(service.get('memswap_limit', memory * 2)),
                    'MemoryReservation': int(service.get('mem_reservation', 0)),
                    'CpusetCpus': service.get('cpuset', ''), 'CpusetMems': '',
                    'PidsLimit': int(service.get('pids_limit', 0)),
                    'BlkioWeight': int(service.get('blkio_config', {}).get('weight', 0)),
                    'RestartPolicy': {'Name': restart[0], 'MaximumRetryCount': int(restart[1]) if len(restart) == 2 else 0}}
        for runtime, compose in [('CpuShares', 'cpu_shares'), ('CpuPeriod', 'cpu_period'), ('CpuQuota', 'cpu_quota'),
                                 ('CpuRealtimePeriod', 'cpu_rt_period'), ('CpuRealtimeRuntime', 'cpu_rt_runtime')]:
            expected[runtime] = int(service.get(compose, 0))
        actual = baseline['HostConfig']
        if any(actual.get(key, '' if isinstance(value, str) else 0) != value for key, value in expected.items()):
            raise ValueError('live MCP resources or restart policy differ from reviewed configuration')

    def candidate(self, candidate_id):
        directory = self.state / identifier(candidate_id)
        trusted_path(directory, 0, directory=True)
        value = root_json(directory / 'candidate.json', 0)
        selected(value['component'])
        if value['id'] != candidate_id or not IMAGE.fullmatch(value['image']):
            raise ValueError('immutable candidate identity required')
        for filename, digest in value['files'].items():
            if Path(filename).name != filename:
                raise ValueError('candidate file outside fixed directory')
            trusted_path(directory / filename, 0)
            if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != digest:
                raise ValueError('candidate configuration changed')
        trusted_path(ENV_FILE, 0)
        if hashlib.sha256(ENV_FILE.read_bytes()).hexdigest() != value['env_sha256']:
            raise ValueError('automation environment changed')
        return directory, value

    def snapshot(self, candidate_id):
        _directory, value = self.candidate(candidate_id)
        row = self.inspect(value['component'])
        return {'id': row['Id'], 'image': row['Image'], 'spec': invariants(row),
                'running': row['State']['Running'], 'health': row['State'].get('Health', {}).get('Status'),
                'compose_hash': row['Config'].get('Labels', {}).get('com.docker.compose.config-hash')}

    def verify(self, candidate_id, *, timeout=120, sleep=time.sleep, monotonic=time.monotonic):
        _directory, value = self.candidate(candidate_id)
        deadline = monotonic() + timeout
        while True:
            row = self.inspect(value['component'])
            if (row['Image'] == value['image'] and row['State']['Running']
                    and row['State'].get('Health', {}).get('Status') == 'healthy'):
                if invariants(row) != value['baseline']['spec']:
                    raise ValueError('MCP runtime configuration changed')
                port = selected(value['component'])[2]
                self.run([DOCKER, 'exec', 'homecompute-automation-n8n-1', 'node', '-e', MCP_PROBE,
                          'http://' + value['component'] + ':' + str(port) + '/mcp'], timeout=60)
                return {'verified': True, 'candidate_id': candidate_id, 'image': value['image']}
            if monotonic() >= deadline:
                raise ValueError('MCP candidate health failed')
            sleep(min(3, max(0, deadline - monotonic())))

    def apply(self, candidate_id):
        directory, value = self.candidate(candidate_id)
        if self.clock() >= value['expires_at'] or self.snapshot(candidate_id) != value['baseline']:
            raise ValueError('MCP baseline changed or expired')
        # Exclusive durable marker prevents direct or newly registered plan retries.
        write(directory / 'attempt.json', {'started_at': self.clock(), 'status': 'attempted'})
        component = value['component']
        command = self.compose(directory)
        stopped = False
        try:
            stopped = True  # A timed-out stop may already have stopped the service.
            self.run(command + ['stop', '--timeout', '20', component], timeout=40)
            archive = directory / 'state.tar'
            fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            self.run([TAR, '--numeric-owner', '-cpf', str(archive), '-C', value['state_path'], '.'], timeout=120)
            with archive.open('rb') as stream:
                os.fsync(stream.fileno())
            write(directory / 'backup.json', {'sha256': digest_file(archive)})
            self.run(command + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', component], timeout=90)
            result = self.verify(candidate_id)
            write(directory / 'result.json', result)
            return result
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            if stopped:
                self.rollback(directory, value)
            raise ValueError('MCP update failed; reconcile recorded rollback before any retry') from None

    def rollback(self, directory, value):
        component = value['component']
        self.run(self.compose(directory) + ['stop', '--timeout', '20', component], timeout=40)
        archive = directory / 'state.tar'
        if (directory / 'backup.json').exists():
            trusted_path(archive, 0)
            if digest_file(archive) != root_json(directory / 'backup.json', 0)['sha256']:
                raise ValueError('backup changed; operator reconciliation required')
            state = Path(value['state_path'])
            for entry in state.iterdir():
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            self.run([TAR, '--numeric-owner', '-xpf', str(archive), '-C', str(state)], timeout=120)
        self.run(self.compose(directory, True) + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never',
                 '--wait', '--wait-timeout', '90', component], timeout=120)
        row = self.inspect(component)
        if row['Image'] != value['baseline']['image'] or invariants(row) != value['baseline']['spec']:
            raise ValueError('rollback runtime differs; operator reconciliation required')
        write(directory / 'rollback.json', {'rolled_back': True, 'image': row['Image']})


def prepare(controller, component, output_path):
    repo, dockerfile, _port = selected(component)
    helper = Maintenance(runner=controller.runner, clock=controller.clock)
    trusted_path(helper.seed, 0, directory=True)
    trusted_path(ENV_FILE, 0)
    baseline = helper.inspect(component)
    if not baseline['State']['Running'] or baseline['State'].get('Health', {}).get('Status') != 'healthy':
        raise ValueError('healthy baseline MCP required')
    installed = helper.run([DOCKER, 'exec', baseline['Id'], 'node', '-p',
                            "require('/app/package.json').version"]).decode().strip()
    commit, version = upstream(component)
    decision = classify_version(installed, version, 'semver')
    if decision['classification'] == 'unchanged':
        raise ValueError('no increasing stable MCP release')
    if decision['classification'] == 'review':
        raise ValueError('unstable or decreasing MCP release requires operator review')
    # A previously attempted installed/candidate pair cannot be retried under a new ID.
    for previous in helper.state.glob('*/candidate.json'):
        old = root_json(previous, 0)
        if (old.get('component'), old.get('installed'), old.get('candidate')) == (component, installed, version) and (previous.parent / 'attempt.json').exists():
            raise ValueError('previous MCP attempt requires operator reconciliation')
    candidate_id = uuid.uuid4().hex
    directory = helper.state / candidate_id
    directory.mkdir(mode=0o700)
    for source in helper.seed.rglob('*'):
        trusted_path(source, 0, directory=source.is_dir())
        target = directory / source.relative_to(helper.seed)
        if source.is_dir():
            target.mkdir(mode=0o700)
        else:
            write(target, source.read_bytes(), raw=True)
    path = directory / dockerfile
    path.write_text(recipe(path.read_text(), commit))
    helper.validate_resources(directory, component, baseline)
    tag = 'homecompute/' + component + ':maintenance-' + candidate_id
    build = [DOCKER, 'build', '--network', 'default', '-f', str(path), '-t', tag, str(directory)]
    if component == 'tilbudstrolden-mcp':
        # Export the reviewed build stage before its production prune, solely for tests.
        text, count = re.subn(r'RUN npm ci && npm run build && npm prune --omit=dev',
                              'RUN npm ci && npm run build', path.read_text().split('\nFROM ', 1)[0])
        if count != 1:
            raise ValueError('reviewed pre-prune test stage changed')
        testfile = directory / 'tests.Dockerfile'
        write(testfile, text.encode(), raw=True)
        testtag = tag + '-tests'
        helper.run([DOCKER, 'build', '-f', str(testfile), '-t', testtag, str(directory)], timeout=600)
        helper.run([DOCKER, 'run', '--rm', '--network', 'none', testtag, 'npm', 'test'], timeout=300)
        helper.run([DOCKER, 'run', '--rm', testtag, 'npm', 'audit', '--omit=dev', '--audit-level=moderate'], timeout=120)
    helper.run(build, timeout=600)
    image = helper.run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', tag]).decode().strip()
    if not IMAGE.fullmatch(image):
        raise ValueError('immutable built image ID required')
    if component == 'aula-mcp':
        helper.run([DOCKER, 'run', '--rm', '--network', 'none', image, 'bun', 'test'], timeout=300)
        audittag = tag + '-audit'
        helper.run([DOCKER, 'build', '--target', 'build', '-f', str(path), '-t', audittag, str(directory)], timeout=600)
        helper.run([DOCKER, 'run', '--rm', audittag, 'pnpm', 'audit', '--prod', '--audit-level', 'moderate'], timeout=120)
    else:
        audittag = tag + '-audit'
        helper.run([DOCKER, 'build', '--target', 'gateway', '-f', str(path), '-t', audittag, str(directory)], timeout=600)
        helper.run([DOCKER, 'run', '--rm', audittag, 'npm', 'audit',
                    '--omit=dev', '--audit-level=moderate'], timeout=120)
    built_version = helper.run([DOCKER, 'run', '--rm', '--network', 'none', image, 'node', '-p',
                                "require('/app/package.json').version"]).decode().strip()
    if built_version != version:
        raise ValueError('built package version differs from reviewed source')
    overlay = (directory / 'production.yaml').read_text()
    pattern = r'(?m)(^  ' + re.escape(component) + r':\n    image: )[^\n]+'
    for filename, reference in [('production.yaml', image), ('rollback.yaml', baseline['Image']),
                                ('baseline.yaml', baseline['Config']['Image'])]:
        text, count = re.subn(pattern, lambda match: match[1] + reference, overlay)
        if count != 1:
            raise ValueError('one fixed MCP image reference required')
        target = directory / filename
        if target.exists():
            target.write_text(text)
        else:
            write(target, text.encode(), raw=True)
    baseline_hash = baseline['Config'].get('Labels', {}).get('com.docker.compose.config-hash')
    if helper.config_hash(directory, component) != baseline_hash:
        raise ValueError('reviewed MCP configuration differs from installed service')
    mounts = [mount for mount in baseline['Mounts'] if mount.get('Destination') == '/data']
    expected_state = '/srv/state/automation/' + component
    if len(mounts) != 1 or mounts[0].get('Type') != 'bind' or mounts[0].get('Source') != expected_state:
        raise ValueError('fixed persistent MCP state mount required')
    trusted_path(Path(expected_state).parent, 0, directory=True)
    state_info = Path(expected_state).lstat()
    if (not Path(expected_state).is_dir() or Path(expected_state).is_symlink()
            or state_info.st_uid not in {0, 1000} or state_info.st_mode & 0o077):
        raise ValueError('trusted existing MCP state directory required')
    expires = controller.clock() + 3600
    value = {'id': candidate_id, 'component': component, 'installed': installed, 'candidate': version,
             'commit': commit, 'repository': repo, 'image': image, 'state_path': expected_state,
             'env_sha256': hashlib.sha256(ENV_FILE.read_bytes()).hexdigest(), 'expires_at': expires,
             'baseline': {'id': baseline['Id'], 'image': baseline['Image'], 'spec': invariants(baseline),
                          'running': True, 'health': 'healthy', 'compose_hash': baseline_hash},
             'files': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                       for name in ('compose.yaml', 'production.yaml', 'rollback.yaml', 'baseline.yaml')}}
    write(directory / 'validation.json', {'upstream_tests': 'passed', 'production_audits': 'passed',
                                        'built_package_version': built_version, 'baseline_config_hash': baseline_hash})
    write(directory / 'candidate.json', value)
    plan = {'schema_version': 1, 'policy': {'operation': 'update', 'target': 'home-core',
            'updates': [{'component': component, 'scheme': 'semver', 'installed': installed, 'candidate': version}]},
            'prechecks': [{'argv': [COMMAND, 'snapshot', candidate_id],
                           'stdout_sha256': hashlib.sha256(output(value['baseline'])).hexdigest()}],
            'steps': [[COMMAND, 'apply', candidate_id]], 'postchecks': [[COMMAND, 'verify', candidate_id]],
            'expires_at': expires, 'timeout_seconds': 900}
    validate_plan(plan)
    write(output_path, plan)
    return {'component': component, 'candidate_id': candidate_id, 'installed': installed, 'candidate': version,
            'plan_file': str(output_path), 'installed_changes': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('snapshot', 'apply', 'verify'))
    parser.add_argument('candidate_id')
    args = parser.parse_args()
    try:
        helper = Maintenance()
        result = getattr(helper, args.operation)(identifier(args.candidate_id))
        sys.stdout.buffer.write(output(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('MCP maintenance refused or uncertain; consult root-only receipts before retry.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
