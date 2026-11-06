#!/usr/bin/env python3
"""Replace the qualified CPP runtime through its existing guarded lifecycles.

The caller owns the deployment, rollout, speech-capacity and Whisper locks.
An outer intake gate spans old rollback, new promotion and recovery.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import sys

PREVIOUS_IMAGE = 'sha256:d6da07a313bc18c336e4ccb9c2680f209d80c079a5fb42e9eb6b522e244448b3'
PREVIOUS_SOURCE = '50ee4f9666c26df89bccab30c83e9cd6dcc0faba'
RECOVERY_RELEASE = '6b5359fbb8af1461aa83e8146af0cc5d88fbd5f1'
JOURNAL = 'cuda-replacement.json'
TERMINAL = {'completed', 'restored'}


def read(api, name):
    path = api.STATE / name
    api.protected(path)
    with path.open('rb') as stream:
        payload = stream.read(1_000_001)
    if len(payload) > 1_000_000:
        raise ValueError('Replacement journal exceeds its bound')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('Replacement journal is malformed')
    return value


def save(api, record):
    target = api.STATE / JOURNAL
    if target.exists() or target.is_symlink():
        api.protected(target)
    with tempfile.NamedTemporaryFile(mode='w', dir=api.STATE, delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        json.dump(record, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def stage(api, record, phase):
    record['phase'] = phase
    save(api, record)


def previous_module(api, revision):
    """Verify the old immutable release before executing any of its Python."""
    if not re.fullmatch(r'[0-9a-f]{40}', str(revision)):
        raise ValueError('Previous transaction must name an exact published release')
    root = Path('/srv/homecompute/releases') / revision
    for path in [*reversed(root.parents), root]:
        api.protected(path, True)
    if (api.run(['git', '-C', str(root), 'rev-parse', 'HEAD']).stdout.strip() != revision or
            api.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all']).stdout.strip() or
            api.run(['git', '-C', str(root), 'remote', 'get-url', 'origin']).stdout.strip() !=
            'https://github.com/madslundt/HomeCompute.git'):
        raise ValueError('Previous release is unavailable, dirty or has a different origin')
    api.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', revision, 'origin/master'])
    spec = importlib.util.spec_from_file_location('_homecompute_previous_whisper', root / 'scripts/setup-whisper-stt.py')
    if spec is None or spec.loader is None:
        raise ValueError('Previous Whisper lifecycle is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if (module.ROOT != root or module.STATE != api.STATE or
            module.CPP_QUALIFIED_IMAGE_ID != PREVIOUS_IMAGE or
            module.CPP_QUALIFIED_SOURCE_REVISION != PREVIOUS_SOURCE or
            module.CPP_MODEL_SHA256 != api.CPP_MODEL_SHA256 or
            module.CPP_RUNTIME_COMMIT != api.CPP_RUNTIME_COMMIT):
        raise ValueError('Previous lifecycle does not own the qualified predecessor')
    return module


def recovery_module(api, *, materialize=False):
    # The active owner's older Compose may allocate an overlapping automatic
    # subnet. Use that owner only to stop; restore with this published network fix.
    if materialize:
        spec = importlib.util.spec_from_file_location(
            '_homecompute_recovery_bootstrap', api.ROOT / 'scripts/bootstrap-whisper-stt.py')
        if spec is None or spec.loader is None:
            raise ValueError('Published recovery bootstrap is unavailable')
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        bootstrap.materialize(RECOVERY_RELEASE)
    return previous_module(api, RECOVERY_RELEASE)


def verify_network(api, owner, revision):
    declared = json.loads(owner.compose_cuda(
        '--profile', 'cuda-candidate', 'config', '--format', 'json', revision=revision,
        image_id=owner.CPP_QUALIFIED_IMAGE_ID).stdout)['networks']['candidate']
    target = [{'subnet': '10.253.73.0/28', 'gateway': '10.253.73.1'}]
    if declared.get('internal') is not True or declared.get('ipam', {}).get('config') != target:
        raise ValueError('Recovery and replacement require the reviewed explicit private subnet')
    networks = [json.loads(api.run(['docker', 'network', 'inspect', identifier]).stdout)[0]
                for identifier in api.run(['docker', 'network', 'ls', '-q']).stdout.split()]
    name = api.CUDA_CANDIDATE_PROJECT + '_candidate'
    own = next((item for item in networks if item['Name'] == name), None)
    if own:
        labels = own.get('Labels') or {}
        candidate = api.inspect(api.CUDA_CANDIDATE)
        expected_containers = {candidate['Id']} if candidate else set()
        if (own.get('Driver') != 'bridge' or own.get('Internal') is not True or
                own.get('EnableIPv6') or own.get('Attachable') or own.get('Options') or
                own.get('IPAM', {}).get('Driver') != 'default' or own.get('IPAM', {}).get('Options') or
                labels.get('com.docker.compose.project') != api.CUDA_CANDIDATE_PROJECT or
                labels.get('com.docker.compose.network') != 'candidate' or
                set(own.get('Containers') or {}) - expected_containers):
            raise ValueError('Candidate network has foreign ownership or endpoints')
    subnet = ipaddress.ip_network(target[0]['subnet'])
    for network in networks:
        if network['Name'] != name and any(
                subnet.overlaps(ipaddress.ip_network(item['Subnet']))
                for item in network.get('IPAM', {}).get('Config') or [] if ':' not in item['Subnet']):
            raise ValueError('Reviewed candidate subnet overlaps another Docker network')
    exact = [{'Subnet': target[0]['subnet'], 'Gateway': target[0]['gateway']}]
    own_bridge = 'br-' + own['Id'][:12] if own and own.get('IPAM', {}).get('Config') == exact else ''
    routes = json.loads(api.run(['ip', '-j', '-4', 'route', 'show', 'table', 'all']).stdout)
    if any(route.get('dst', 'default') != 'default' and route.get('dev') != own_bridge and
           subnet.overlaps(ipaddress.ip_network(route['dst'], strict=False)) for route in routes):
        raise ValueError('Reviewed candidate subnet overlaps a host route')


def maintenance_bundle(owner, *, installed=False):
    command = [sys.executable, '-I', '-B', str(owner.ROOT / 'scripts/setup-infrastructure-maintenance.py'),
               '--source', str(owner.ROOT / 'scripts')]
    preview = json.loads(owner.run(command, timeout=120).stdout)
    digest = preview.get('bundle_sha256')
    if preview.get('installed') is not False or not re.fullmatch(r'[0-9a-f]{64}', str(digest)):
        raise ValueError('Exact published predecessor maintenance bundle required')
    if installed:
        verified = json.loads(owner.run([*command, '--verify-bundle-sha256', digest], timeout=120).stdout)
        if (verified.get('host') != 'home-spark' or verified.get('verified') is not True or
                verified.get('bundle_sha256') != digest):
            raise ValueError('Installed maintenance differs from the published predecessor; replacement refused')
    return digest


def maintenance_artifacts(owner):
    spec = importlib.util.spec_from_file_location(
        '_homecompute_maintenance_bundle', owner.ROOT / 'scripts/setup-infrastructure-maintenance.py')
    if spec is None or spec.loader is None:
        raise ValueError('Published maintenance verifier is unavailable')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    files, digest = installer.bundle(owner.ROOT / 'scripts')
    artifacts = {installer.DEST / name: (data, 0o644) for name, data in files.items()}
    for name, source in (('homecompute-container-maintenance', 'container-maintenance.py'),
                         ('homecompute-infrastructure-maintenance-spark', 'infrastructure-maintenance-spark.py')):
        contents = '#!/usr/bin/python3 -I\n' + files[source].decode().split('\n', 1)[1]
        artifacts[installer.LIBEXEC / name] = (contents.encode(), 0o755)
    return installer, digest, artifacts


def read_installed(path, mode):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != mode or info.st_size > 131072):
            raise ValueError('Installed maintenance ownership or mode changed; recovery held')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            return stream.read(131073)
    finally:
        os.close(descriptor)


def verify_maintenance_transition(api, recovery, record):
    """Allow only old/new exact files, including our own interrupted atomic install."""
    _old, old_digest, old_files = maintenance_artifacts(recovery)
    target, target_digest, target_files = maintenance_artifacts(api)
    if (old_digest != record.get('previous_maintenance_bundle_sha256') or
            target_digest != record.get('target_maintenance_bundle_sha256') or
            set(old_files) != set(target_files)):
        raise ValueError('Recorded maintenance transition source changed; recovery held')
    for directory in {path.parent for path in old_files}:
        target.trusted_path(directory, 0, directory=True)
        if stat.S_IMODE(directory.stat().st_mode) != 0o755:
            raise ValueError('Installed maintenance directory mode changed; recovery held')
    for path, (old_data, old_mode) in old_files.items():
        new_data, new_mode = target_files[path]
        if old_mode != new_mode:
            raise ValueError('Maintenance transition file modes differ; recovery held')
        target.trusted_path(path, 0)
        if read_installed(path, old_mode) not in (old_data, new_data):
            raise ValueError('Installed maintenance has unrelated changes; recovery held')


def healthy(item):
    return bool(item and item.get('State', {}).get('Running') and
                item.get('State', {}).get('Health', {}).get('Status') == 'healthy')


def available_memory():
    return next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                if line.startswith('MemAvailable:'))


def image_available(api, image_id):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', str(image_id)):
        raise ValueError('Exact retained image identity is required')
    result = json.loads(api.run(['docker', 'image', 'inspect', image_id]).stdout)
    if len(result) != 1 or result[0].get('Id') != image_id:
        raise ValueError('Exact retained image is unavailable')


def verify_promoted(api, image_id, source_revision):
    tx = read(api, 'cuda-candidate.json')
    candidate = api.inspect(api.CUDA_CANDIDATE)
    relay = api.inspect('gb10-whisper-cpp-http')
    labels = (candidate or {}).get('Config', {}).get('Labels') or {}
    relay_labels = (relay or {}).get('Config', {}).get('Labels') or {}
    networks = (candidate or {}).get('NetworkSettings', {}).get('Networks') or {}
    inference = 'whisper-large-v3-stt_inference'
    relay_networks = (relay or {}).get('NetworkSettings', {}).get('Networks') or {}
    if (tx.get('state') != 'promoted' or tx.get('promotion') is not True or
            tx.get('candidate_image_id') != image_id or tx.get('image_source_revision') != source_revision or
            not healthy(candidate) or candidate.get('Image') != image_id or
            labels.get('com.docker.compose.project') != api.CUDA_CANDIDATE_PROJECT or
            labels.get('com.docker.compose.service') != 'whisper-cpp-cuda' or
            api.restart_policy(candidate) != 'unless-stopped' or
            not healthy(relay) or relay.get('Id') != tx.get('relay_candidate_id') or
            relay.get('Image') != tx.get('relay_candidate_image_id') or
            relay_labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
            relay_labels.get('com.docker.compose.service') != 'whisper-cpp-http' or
            api.restart_policy(relay) != 'unless-stopped' or
            set(networks) != {api.CUDA_CANDIDATE_PROJECT + '_candidate', inference} or
            not {'whisper-primary', 'whisper-cpp-recording'} <= set(networks.get(inference, {}).get('Aliases') or []) or
            set(relay_networks) != {'whisper-large-v3-stt_edge', inference}):
        raise ValueError('Exact promoted model and relay are not healthy and owned')
    if api.gate_is_active(tx['chain']):
        raise ValueError('Promoted runtime still has an active intake gate')
    return tx


def preflight(api, revision):
    api.guards()
    if api.CPP_QUALIFIED_IMAGE_ID == PREVIOUS_IMAGE:
        raise ValueError('Replacement image has not been qualified in this release')
    api.qualified_cuda_candidate_image_id()
    api.verify_cpp_model(api.CPP_MODEL_PATH)
    tx = verify_promoted(api, PREVIOUS_IMAGE, PREVIOUS_SOURCE)
    if api.gate_is_active(tx['chain']):
        raise ValueError('Recover the predecessor intake gate before replacement')
    old = previous_module(api, tx.get('revision'))
    old.qualified_cuda_candidate_image_id()
    recovery = recovery_module(api, materialize=True)
    recovery.qualified_cuda_candidate_image_id()
    verify_network(api, api, revision)
    verify_network(api, recovery, RECOVERY_RELEASE)
    bundle = maintenance_bundle(recovery, installed=True)
    if tx.get('maintenance_bundle_sha256') != bundle:
        raise ValueError('Predecessor journal and installed maintenance bundle differ; replacement refused')
    image_available(api, tx.get('relay_candidate_image_id'))
    primary = api.inspect(tx.get('primary_id'))
    original_relay = api.inspect((tx.get('relay_original') or {}).get('id'))
    if (not primary or primary.get('Image') != tx.get('primary_image') or
            primary.get('Name') != '/gb10-whisper-primary' or primary.get('State', {}).get('Running') or
            not original_relay or original_relay.get('Image') != tx['relay_original'].get('image')):
        raise ValueError('Exact retained vLLM and relay are required for guarded rollback')
    # The old lifecycle briefly restores this bounded engine before promotion.
    command = primary.get('Config', {}).get('Cmd') or []
    try:
        utilization = command[command.index('--gpu-memory-utilization') + 1]
    except (ValueError, IndexError):
        raise ValueError('Retained vLLM memory reservation is unknown') from None
    if (utilization != '0.08' or command.count('--gpu-memory-utilization') != 1 or
            primary.get('HostConfig', {}).get('Memory') != 12 * 1024**3):
        raise ValueError('Retained vLLM capacity differs from the reviewed transition')
    if available_memory() < 12 * 1024**3:
        raise ValueError('Less than 12 GiB available before predecessor shutdown')
    return old, tx, bundle, maintenance_bundle(api)


def finish(api, record, phase):
    api.gate(record['chain'], False)
    if api.gate_is_active(record['chain']):
        raise ValueError('Replacement intake gate remains active')
    record['gate_held'] = False
    stage(api, record, phase)


def recover(api, revision, record=None):
    record = record or read(api, JOURNAL)
    if record.get('revision') != revision or record.get('target_image') != api.CPP_QUALIFIED_IMAGE_ID:
        raise ValueError('Replacement recovery belongs to a different release')
    previous_revision = record['previous']['revision']
    old = previous_module(api, previous_revision)
    recovery = recovery_module(api)
    if (record.get('recovery_release') != RECOVERY_RELEASE or
            maintenance_bundle(recovery) != record.get('previous_maintenance_bundle_sha256') or
            record['previous'].get('maintenance_bundle_sha256') != record.get('previous_maintenance_bundle_sha256')):
        raise ValueError('Recorded predecessor maintenance or recovery release changed')
    verify_maintenance_transition(api, recovery, record)
    image_available(api, record['previous']['relay_candidate_image_id'])
    recovery.qualified_cuda_candidate_image_id()
    verify_network(api, recovery, RECOVERY_RELEASE)
    api.gate(record['chain'], True)
    record['gate_held'] = True
    stage(api, record, 'recovering')
    tx = read(api, 'cuda-candidate.json')
    owners = {revision: (api.CPP_QUALIFIED_IMAGE_ID, api),
              previous_revision: (PREVIOUS_IMAGE, old),
              RECOVERY_RELEASE: (PREVIOUS_IMAGE, recovery)}
    owner = owners.get(tx.get('revision'))
    if owner is None or tx.get('candidate_image_id') != owner[0]:
        raise ValueError('Unexpected active transaction; recovery held')
    inner_gate = api.gate_is_active(tx['chain'])
    if tx.get('state') == 'promoted' and tx.get('candidate_image_id') == PREVIOUS_IMAGE and not inner_gate:
        verify_promoted(api, PREVIOUS_IMAGE, PREVIOUS_SOURCE)
    else:
        # A completed container rollback may still have failed gate cleanup.
        # Never archive that journal until its owner has verified a clear gate.
        if tx.get('state') != 'rolled-back' or inner_gate:
            owner[1].cuda_candidate_stop(tx['revision'])
        stopped = read(api, 'cuda-candidate.json')
        if (stopped.get('revision') != tx['revision'] or stopped.get('candidate_image_id') != owner[0] or
                stopped.get('state') != 'rolled-back' or api.gate_is_active(stopped['chain'])):
            raise ValueError('Inner rollback or intake gate is not complete; recovery held')
        # Restore exact retained artifacts with the published explicit subnet.
        bundle = recovery.ensure_spark_maintenance_ownership()
        if bundle != record['previous_maintenance_bundle_sha256']:
            raise ValueError('Recovery maintenance bundle changed')
        recovery.cuda_candidate_start(RECOVERY_RELEASE, max_audio_seconds=1200,
                                     max_upload_bytes=40_000_000, native_timeout_seconds=1800,
                                     promotion=True,
                                     relay_image=record['previous']['relay_candidate_image_id'],
                                     relay_image_id=record['previous']['relay_candidate_image_id'],
                                     maintenance_bundle_sha256=bundle)
        restored = verify_promoted(api, PREVIOUS_IMAGE, PREVIOUS_SOURCE)
        if restored['revision'] != RECOVERY_RELEASE:
            raise ValueError('Recovery promotion is not owned by its published safe-network release')
    if recovery.ensure_spark_maintenance_ownership() != record['previous_maintenance_bundle_sha256']:
        raise ValueError('Recovery maintenance bundle changed')
    finish(api, record, 'restored')
    return {'state': 'restored', 'revision': revision, 'gate_held': False}


def replace(api, revision):
    path = api.STATE / JOURNAL
    if path.exists() or path.is_symlink():
        previous = read(api, JOURNAL)
        if previous.get('revision') != revision:
            raise ValueError('Inspect the existing replacement with its owning release')
        if previous.get('phase') == 'completed':
            verify_promoted(api, api.CPP_QUALIFIED_IMAGE_ID, api.CPP_QUALIFIED_SOURCE_REVISION)
            finish(api, previous, 'completed')
            return {'state': 'completed', 'revision': revision, 'gate_held': False}
        if previous.get('phase') != 'restored':
            return recover(api, revision, previous)
        path.rename(api.STATE / ('cuda-replacement-restored-' + os.urandom(6).hex() + '.json'))
    old, tx, bundle, target_bundle = preflight(api, revision)
    record = {'schema_version': 1, 'revision': revision, 'target_image': api.CPP_QUALIFIED_IMAGE_ID,
              'previous': tx, 'recovery_release': RECOVERY_RELEASE,
              'previous_maintenance_bundle_sha256': bundle, 'target_maintenance_bundle_sha256': target_bundle,
              'phase': 'prepared', 'chain': 'HC-STT-' + os.urandom(6).hex(),
              'gate_held': False}
    save(api, record)
    try:
        api.gate(record['chain'], True)
        record['gate_held'] = True
        stage(api, record, 'stopping-predecessor')
        old.cuda_candidate_stop(tx['revision'])
        stage(api, record, 'promoting-replacement')
        api.promote_qualified_cuda_candidate(revision)
        verify_promoted(api, api.CPP_QUALIFIED_IMAGE_ID, api.CPP_QUALIFIED_SOURCE_REVISION)
        finish(api, record, 'completed')
    except BaseException as error:
        record['failure_class'] = type(error).__name__
        stage(api, record, 'recovery-required')
        try:
            recover(api, revision, record)
        except BaseException as recovery_error:
            record['recovery_failure_class'] = type(recovery_error).__name__
            stage(api, record, 'recovery-required')
            raise RuntimeError('Replacement recovery held; retry through the same published release') from None
        raise RuntimeError('Replacement failed; previous qualified runtime restored') from None
    return {'state': 'completed', 'revision': revision, 'gate_held': False}


def status(api, revision):
    record = read(api, JOURNAL)
    if record.get('revision') != revision:
        raise ValueError('Replacement belongs to a different release')
    return {'revision': revision, 'state': record.get('phase'),
            'gate_held': api.gate_is_active(record['chain']),
            'failure_class': record.get('failure_class'),
            'recovery_failure_class': record.get('recovery_failure_class')}


def require_no_pending(api):
    if (api.STATE / JOURNAL).exists() or (api.STATE / JOURNAL).is_symlink():
        record = read(api, JOURNAL)
        if record.get('phase') not in TERMINAL or api.gate_is_active(record['chain']):
            raise ValueError('Recover the pending CPP replacement through its owning release first')
