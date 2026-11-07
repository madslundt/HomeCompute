"""Bounded legacy Docker installer build; no model imports, weights or network."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_observer_build_control', ROOT / 'scripts/language_observer_control.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
A, PIN = C.A, C.PIN
FILES = ('Dockerfile', '.dockerignore', 'common.py', 'graph.py', 'export.py', 'classify.py',
         'installer_check.py', 'model.json', 'dependencies.json', 'requirements.lock')
BUILD_LABEL = 'io.homecompute.language-observer.build-owner'


def arguments(auth: dict, context: Path, owner: str, tag: str) -> list[str]:
    return ['docker', 'build', '--pull=false', '--force-rm', '--rm=true', '--no-cache', '--network=none',
            '--platform=linux/arm64', '--memory=' + str(C.LIMIT), '--memory-swap=' + str(C.LIMIT),
            '--cpu-period=100000', '--cpu-quota=400000', '--build-arg', 'BASE_IMAGE=' + PIN['base_image_id'],
            '--build-arg', 'OBSERVER_BUILD_OWNER=' + owner, '--build-arg',
            'HOME_COMPUTE_REVISION=' + auth['source_revision'], '--tag', tag, str(context)]


def containers(owner: str, source: str, *, cleanup: bool = False) -> list[str]:
    result = C.run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=' + BUILD_LABEL + '=' + owner])
    identities = result.stdout.decode().split()
    verified = []
    for identity in identities:
        probe = C.run(['docker', 'container', 'inspect', identity], check=False)
        if probe.returncode:
            C.require(b'No such container' in probe.stderr or b'No such object' in probe.stderr, 'Builder absence uncertain')
            continue
        row = json.loads(probe.stdout)[0]
        labels, host = row['Config'].get('Labels') or {}, row['HostConfig']
        C.require(row['Id'] == identity and labels.get(BUILD_LABEL) == owner and
                  labels.get('io.homecompute.source-revision') == source, 'Build container ownership differs')
        installer = (row['State']['Running'] and 'installer_check.py snapshot /tmp/nvidia-packages.json' in
                     ' '.join(row['Config'].get('Cmd') or []))
        if not cleanup and not installer:
            continue
        if not cleanup:
            C.require(host['Memory'] == C.LIMIT and
                  host['MemorySwap'] == C.LIMIT and host['CpuPeriod'] == 100000 and host['CpuQuota'] == 400000 and
                  host['NetworkMode'] == 'none' and not host.get('DeviceRequests') and not host.get('Devices') and
                  all(item in row['Config']['Env'] for item in ('CUDA_VISIBLE_DEVICES=', 'NVIDIA_VISIBLE_DEVICES=void')),
                      'Actual owned legacy installer container isolation differs')
        verified.append(identity)
        if cleanup:
            if row['State']['Running']:
                C.run(['docker', 'stop', '--time', '5', identity], timeout=15)
            C.run(['docker', 'rm', identity])
    return verified


def build(auth: dict, output: Path) -> dict:
    C.external(str(output.parent))
    C.require(str(output) == auth['build_receipt'] and not output.exists(), 'Frozen new build receipt required; no retry')
    context = Path(auth['build_context'])
    C.external(str(context.parent))
    C.require(not context.exists() and not context.is_symlink(), 'Frozen new external build context required')
    artifacts = C.external(auth['public_artifacts'])
    verified = A.verify(artifacts)
    base = json.loads(C.run(['docker', 'image', 'inspect', PIN['base_image_id']]).stdout)[0]
    C.require(base['Id'] == PIN['base_image_id'] and base['Architecture'] == 'arm64' and base['Os'] == 'linux',
              'Exact cached NVIDIA ARM64 base required; no pull')
    tag = 'homecompute-language-observer:' + auth['source_revision']
    prior = C.run(['docker', 'image', 'inspect', tag], check=False)
    C.require(prior.returncode != 0 and b'No such image' in prior.stderr, 'Existing/uncertain image requires review')
    C.memory(loading=True)
    C.require(shutil.disk_usage(context.parent).free >= PIN['disk_reserve_bytes'] + A.pins()[1]['wheel_bytes'],
              'Exact build context disk reserve insufficient')
    owner = secrets.token_hex(12)
    state = {'state': 'attempted', 'source_revision': auth['source_revision'], 'retry_allowed': False,
             'build_owner': owner, 'context': str(context), 'model_loads': 0, 'legacy_builder': True,
             'memory_bytes': C.LIMIT, 'swap_bytes': 0, 'cpus': 4, 'deadline_seconds': 600}
    A.create(output, state)
    process = None
    try:
        context.mkdir(mode=0o700)
        for name in FILES:
            shutil.copyfile(A.SOURCE / name, context / name, follow_symlinks=False)
            C.require(A.sha((context / name).read_bytes()) == A.sha((A.SOURCE / name).read_bytes()), 'Source context differs')
        (context / 'wheels').mkdir(mode=0o700)
        for item in A.catalog():
            if item['group'] == 'wheels':
                target = context / 'wheels' / item['name']
                shutil.copyfile(artifacts / 'wheels' / item['name'], target, follow_symlinks=False)
                A.verify_file(target, item)
        C.require(shutil.disk_usage(context.parent).free >= PIN['disk_reserve_bytes'], 'Build disk reserve breached')
        log = output.with_suffix(output.suffix + '.log')
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        observed = set()
        with os.fdopen(fd, 'wb') as stream:
            deadline = time.monotonic() + 600
            process = subprocess.Popen(arguments(auth, context, owner, tag), stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       env={**os.environ, 'DOCKER_BUILDKIT': '0'})
            while process.poll() is None:
                C.memory()
                C.require(time.monotonic() < deadline and os.fstat(stream.fileno()).st_size <= 2_000_000,
                          'Installer build deadline/output cap reached')
                observed.update(containers(owner, auth['source_revision']))
                time.sleep(.25)
            C.require(process.returncode == 0 and time.monotonic() < deadline and
                      os.fstat(stream.fileno()).st_size <= 2_000_000, 'Bounded installer build failed')
        C.memory()
        C.require(observed, 'Actual bounded installer container was never observed')
        state['observed_bounded_installer_containers'] = sorted(observed)
        C.require(not containers(owner, auth['source_revision']), 'Legacy build left owned containers')
        image = json.loads(C.run(['docker', 'image', 'inspect', tag]).stdout)[0]
        labels = image['Config']['Labels']
        C.require(labels['io.homecompute.source-revision'] == auth['source_revision'] and
                  labels[BUILD_LABEL] == owner and labels['io.homecompute.language-observer.base'] == base['Id'],
                  'Built source identity differs')
        state.update(state='complete', image_id=image['Id'], base_image_id=base['Id'],
                     artifact_verification=verified, build_log_sha256=A.sha(log.read_bytes()),
                     classification_authorized=False,
                     source_files_sha256={name: A.sha((context / name).read_bytes()) for name in FILES})
        A.write(output, state)
        return state
    except BaseException as error:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        state.update(state='failed', failure_class=type(error).__name__, forced_termination_unknown=True)
        try:
            state['owned_cleanup_ids'] = containers(owner, auth['source_revision'], cleanup=True)
            state['owned_cleanup_verified'] = not containers(owner, auth['source_revision'])
        except Exception as cleanup_error:
            state['cleanup_failure_class'] = type(cleanup_error).__name__
            state['owned_cleanup_verified'] = False
        A.write(output, state)
        raise
