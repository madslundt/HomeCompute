"""Finite root preparation selectors; assistant input cannot choose commands or URLs."""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid
from typing import Any

from docker_image_updates import DIGEST, reference
from infrastructure_maintenance import ENV, HA_COMMAND, MAX_JSON, MAX_OUTPUT, SPARK_COMMAND, canonical, identifier, root_json, trusted_path, validate_plan
from infrastructure_update_policy import classify_plan

ROOT = Path(__file__).resolve().parent
REPORT = Path('/var/lib/homecompute-maintenance/report.json')
CONTAINER_COMMAND = '/usr/local/libexec/homecompute-container-maintenance'
SPARK_COMPONENTS = {'packages', 'restart-plapre', 'restart-hviske', 'restart-qwen'}
SSH = '/run/current-system/sw/bin/ssh'
SSH_ARGS = ['-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=8',
            '-o', 'UserKnownHostsFile=/var/lib/homecompute-infrastructure-maintenance/ssh/known_hosts',
            '-i', '/var/lib/homecompute-infrastructure-maintenance/ssh/id_ed25519', 'root@192.168.30.126']
DOCKER = {
    'homepage': ('homecompute-homepage-homepage-1', 'homepage', ['compose.yaml'], 'homepage.env'),
    'n8n': ('homecompute-automation-n8n-1', 'automation', ['compose.yaml', 'production.yaml'], 'automation.env'),
    'caddy': ('homecompute-control-plane-caddy-1', 'control-plane', ['compose.yaml'], 'control-plane.env'),
    'litellm': ('homecompute-control-plane-litellm-1', 'control-plane', ['compose.yaml'], 'control-plane.env'),
    'postgres': ('homecompute-control-plane-postgres-1', 'control-plane', ['compose.yaml'], 'control-plane.env'),
    'open-webui': ('homecompute-open-webui-open-webui-1', 'open-webui', ['compose.yaml'], 'open-webui.env'),
    'immich': ('homecompute-immich-immich-server-1', 'immich', ['compose.yaml'], 'immich.env'),
    'piper': ('home-core-piper-tts', 'piper-tts', ['compose.yaml'], 'piper-tts.env'),
    'stt': ('home-core-wyoming-stt', 'wyoming-stt', ['compose.yaml'], 'wyoming-stt.env'),
}
MCPS = {'aula-mcp', 'tilbudstrolden-mcp'}
MODULES = {'core': 'prepare-core-maintenance.py', 'docker': 'prepare-docker-maintenance.py',
           'spark': 'prepare-spark-maintenance.py', 'ha': 'homeassistant-maintenance.py'}


def module(kind: str) -> Any:
    spec = importlib.util.spec_from_file_location('hc_prepare_' + kind, ROOT / MODULES[kind])
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def decode(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_JSON:
        raise ValueError('bounded trusted JSON receipt required')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate receipt fields')
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('trusted receipt object required')
    return value


def read(controller: Any, argv: list[str], timeout: int = 180) -> bytes:
    result = controller.runner(argv, timeout=timeout, env=ENV.copy(), cwd='/', shell=False)
    if (result.returncode or not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes)
            or len(result.stdout) + len(result.stderr) > MAX_OUTPUT):
        raise ValueError('trusted preparation command unavailable')
    return result.stdout


def destination(controller: Any) -> Path:
    path = controller.state_dir / 'prepared'
    path.mkdir(mode=0o700, exist_ok=True)
    trusted_path(path, 0, directory=True)
    return path / (uuid.uuid4().hex + '.json')


def ssh(operation: str, selected: str, digest: str | None = None) -> list[str]:
    if operation == 'prepare':
        if selected not in SPARK_COMPONENTS and not re.fullmatch(r'image-[A-Za-z0-9][A-Za-z0-9_.-]{0,110}', selected):
            raise ValueError('Spark component outside finite preparation catalogue')
    elif operation not in {'check', 'run', 'status', 'verify', 'approve'}:
        raise ValueError('unsupported Spark operation')
    else:
        identifier(selected)
    if operation == 'approve':
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('exact remote approval digest required')
    elif digest is not None:
        raise ValueError('digest belongs only to operator approval')
    suffix = ' ' + digest if digest is not None else ''
    return [SSH, *SSH_ARGS, SPARK_COMMAND + ' ' + operation + ' ' + selected + suffix]


def remote_receipt(raw: bytes, now: float) -> dict[str, Any]:
    receipt = decode(raw)
    keys = {'plan_id', 'digest', 'status', 'reason', 'expires_at', 'policy', 'updates_truncated',
            'requires_approval', 'classification', 'approved'}
    if set(receipt) != keys:
        raise ValueError('exact Spark receipt required')
    identifier(receipt['plan_id'])
    if not isinstance(receipt['digest'], str) or not re.fullmatch('[a-f0-9]{64}', receipt['digest']):
        raise ValueError('exact remote plan digest required')
    if (receipt['status'] != 'registered' or receipt['reason'] is not None or receipt['updates_truncated'] is not False
            or type(receipt['requires_approval']) is not bool or type(receipt['approved']) is not bool
            or type(receipt['expires_at']) not in {int, float} or not math.isfinite(receipt['expires_at'])
            or not now < receipt['expires_at'] <= now + 3600):
        raise ValueError('complete live unstarted Spark plan required')
    expected = classify_plan(receipt['policy'])
    if (receipt['policy']['target'] != 'home-spark'
            or len(receipt['policy']['updates']) > 120
            or (receipt['policy']['operation'] == 'update' and not
                (all(row['scheme'] == 'debian' for row in receipt['policy']['updates']) or
                 len(receipt['policy']['updates']) == 1 and receipt['policy']['updates'][0]['scheme'] == 'semver'))
            or (receipt['policy']['operation'] == 'fix' and receipt['policy']['updates'])):
        raise ValueError('remote package policy mismatch')
    if receipt['requires_approval'] or expected['classification'] == 'unchanged':
        expected = dict(expected, approval_required=True, reason='root_plan_requires_approval')
    actual = receipt['classification']
    if not isinstance(actual, dict) or set(actual) - {'updates'} != set(expected) - {'updates'}:
        raise ValueError('remote classification contract mismatch')
    if {k: v for k, v in actual.items() if k != 'updates'} != {k: v for k, v in expected.items() if k != 'updates'}:
        raise ValueError('remote classification disagrees with trusted policy')
    return receipt


def remote_plan(controller: Any, component: str = 'packages') -> dict[str, Any]:
    receipt = remote_receipt(read(controller, ssh('prepare', component)), controller.clock())
    if receipt['policy']['operation'] != ('update' if component == 'packages' or component.startswith('image-') else 'fix'):
        raise ValueError('remote operation differs from selected component')
    updates = receipt['policy']['updates']
    if component == 'packages' and not all(row['scheme'] == 'debian' for row in updates):
        raise ValueError('remote package selector returned a different update kind')
    if component.startswith('image-') and (len(updates) != 1 or updates[0]['scheme'] != 'semver'
            or updates[0]['component'] != component.removeprefix('image-')):
        raise ValueError('remote image selector returned a different container')
    remote_id = receipt['plan_id']
    current = read(controller, ssh('check', remote_id))
    expected = {key: value for key, value in receipt.items() if key != 'approved'}
    if decode(current) != expected:
        raise ValueError('Spark preparation snapshot changed')
    return {'schema_version': 1, 'policy': receipt['policy'], 'requires_approval': receipt['requires_approval'],
            'prechecks': [{'argv': ssh('check', remote_id), 'stdout_sha256': hashlib.sha256(current).hexdigest()}],
            'steps': [ssh('run', remote_id)],
            'postchecks': [ssh('verify', remote_id)],
            'timeout_seconds': 900, 'expires_at': min(receipt['expires_at'], controller.clock() + 3600)}


def approve_remote(controller: Any, row: Any) -> None:
    """Invoked only by the independent root approve operation, never preparation."""
    if os.geteuid() != 0 or controller.uid != 0:
        raise ValueError('root operator required for remote approval')
    plan = json.loads(row['plan'])
    if plan['policy']['target'] != 'home-spark' or controller.local_target != 'home-core':
        return
    if len(plan['steps']) != 1 or len(plan['prechecks']) != 1:
        raise ValueError('one fixed remote wrapper required')
    match = re.fullmatch(re.escape(SPARK_COMMAND) + r' run ([a-f0-9]{32})', plan['steps'][0][-1])
    if not match or plan['steps'][0] != ssh('run', match[1]):
        raise ValueError('remote approval wrapper identity mismatch')
    selected = match[1]
    if plan['prechecks'][0]['argv'] != ssh('check', selected) or plan['postchecks'] != [ssh('verify', selected)]:
        raise ValueError('remote approval check/verification mismatch')
    checked = read(controller, ssh('check', selected))
    if hashlib.sha256(checked).hexdigest() != plan['prechecks'][0]['stdout_sha256']:
        raise ValueError('remote approval snapshot changed')
    receipt = remote_receipt(read(controller, ssh('status', selected)), controller.clock())
    if (receipt['policy'] != plan['policy'] or receipt['requires_approval'] != plan.get('requires_approval', False)
            or decode(checked) != {key: value for key, value in receipt.items() if key != 'approved'}):
        raise ValueError('remote approval policy changed')
    approved = remote_receipt(read(controller, ssh('approve', selected, receipt['digest'])), controller.clock())
    if approved != dict(receipt, approved=True):
        raise ValueError('remote approval receipt mismatch')


def image_candidate(component: str, now: float) -> str:
    report = root_json(REPORT, 0)
    if (type(report.get('schema_version')) is not int or report.get('schema_version') != 1 or report.get('host') != 'home-core'
            or report.get('mode') != 'observe-only' or report.get('automatic_actions') is not False):
        raise ValueError('trusted core maintenance report required')
    def fresh(value):
        if not isinstance(value, str):
            raise ValueError('report timestamp required')
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or not 0 <= now - stamp.timestamp() <= 86400:
            raise ValueError('Docker update source must be at most 24 hours old')
    fresh(report.get('generated_at'))
    image_report = report.get('docker_image_updates')
    if not isinstance(image_report, dict):
        raise ValueError('trusted Docker image report required')
    images = image_report.get('images')
    if not isinstance(images, list) or len(images) > 64:
        raise ValueError('bounded trusted image inventory required')
    rows = [row for row in images if isinstance(row, dict) and row.get('container') == DOCKER[component][0]]
    if len(rows) != 1 or rows[0].get('host') != 'home-core' or rows[0].get('state') != 'update_available':
        raise ValueError('one observed component update required')
    row = rows[0]
    fresh(row.get('checked_at'))
    parsed = reference(row.get('image'))
    digest = row.get('remote_index_digest')
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError('exact observed registry candidate required')
    if (row.get('registry'), row.get('repository')) != (parsed['registry'], parsed['repository']):
        raise ValueError('observed repository provenance mismatch')
    return parsed['registry'] + '/' + parsed['repository'] + '@' + digest


def docker_inputs(controller: Any, component: str, docker: str) -> tuple[list[Path], Path]:
    container, directory, files, environment = DOCKER[component]
    labels = decode(read(controller, [docker, 'inspect', '--format', '{{json .Config.Labels}}', container]))
    configured = labels.get('com.docker.compose.project.config_files')
    if not isinstance(configured, str):
        raise ValueError('existing exact Docker Compose source labels required')
    paths = configured.split(',')
    route = '/srv/state/openclaw-communication/deployment/n8n-private-route.yaml'
    if component == 'n8n' and paths[-1] == route:
        standard, extra = paths[:-1], [Path(route)]
    else:
        standard, extra = paths, []
    if len(standard) != len(files):
        raise ValueError('all reviewed Compose sources must be supplied in order')
    releases = []
    for selected, basename in zip(standard, files):
        pattern = r'/srv/homecompute/releases/([A-Za-z0-9_.-]{1,128})/deploy/' + re.escape(directory + '/' + basename)
        match = re.fullmatch(pattern, selected)
        if not match or match[1] in {'.', '..'}:
            raise ValueError('Docker source outside finite root release catalogue')
        releases.append(match[1])
    if len(set(releases)) != 1:
        raise ValueError('Compose sources must belong to one reviewed release')
    sources = [trusted_path(Path(path), 0) for path in standard] + [trusted_path(path, 0) for path in extra]
    env = labels.get('com.docker.compose.project.environment_file', '/etc/homecompute/' + environment)
    accepted = {'/etc/homecompute/' + environment}
    if component == 'n8n':
        accepted.add('/etc/homecompute/automation-tunnel.env')
    if not isinstance(env, str) or env not in accepted:
        raise ValueError('Docker environment outside reviewed root catalogue')
    return sources, trusted_path(Path(env), 0)


def prepare(controller: Any, target: str, component: str) -> dict[str, Any]:
    if os.geteuid() != 0 or controller.uid != 0:
        raise ValueError('trusted root preparation required')
    if not isinstance(target, str) or target not in {'home-core', 'home-spark', 'home-assistant'} or not isinstance(component, str):
        raise ValueError('finite infrastructure selectors required')
    image = bool(re.fullmatch(r'image-[A-Za-z0-9][A-Za-z0-9_.-]{0,110}', component))
    if ((target == 'home-core' and not image and component not in {'nixos', *DOCKER, *MCPS, *('restart-' + key for key in DOCKER)})
            or (target == 'home-spark' and not image and component not in SPARK_COMPONENTS)
            or (target == 'home-assistant' and component not in module('ha').ENTITIES)
            or (controller.local_target == 'home-spark' and target != 'home-spark')):
        raise ValueError('component outside trusted preparation catalogue')
    output = destination(controller)
    if image and target == controller.local_target:
        import host_maintenance_sources
        host_maintenance_sources.prepare(controller, component.removeprefix('image-'), output)
    elif target == 'home-core' and component in MCPS:
        import mcp_maintenance
        mcp_maintenance.prepare(controller, component, output)
    elif target == 'home-assistant':
        receipt = decode(read(controller, [HA_COMMAND, 'prepare', component, '--output', str(output)]))
        if receipt != {'plan_file': str(output), 'installed_changes': False}:
            raise ValueError('HA preparation receipt mismatch')
    elif target == 'home-spark' and controller.local_target == 'home-core':
        plan = remote_plan(controller, component)
        validate_plan(plan)
        module('core').write_plan(output, plan)
    elif target == 'home-spark':
        if component != 'packages':
            raise ValueError('Spark restarts are prepared by its fixed local dispatcher')
        helper = module('spark')
        plan, _ = helper.prepare(runner=controller.runner, clock=controller.clock)
        helper.write_plan(output, plan)
    elif component == 'nixos':
        import core_update_build
        core_update_build.prepare(controller, output)
    elif component.startswith('restart-'):
        receipt = decode(read(controller, [CONTAINER_COMMAND, 'prepare', DOCKER[component[8:]][0], '--output', str(output)]))
        if receipt != {'plan_file': str(output), 'installed_changes': False}:
            raise ValueError('container recovery preparation receipt mismatch')
    else:
        candidate = image_candidate(component, controller.clock())
        container = DOCKER[component][0]
        docker = shutil.which('docker', path=ENV['PATH'])
        if not docker:
            raise ValueError('trusted Docker executable unavailable')
        compose_files, environment = docker_inputs(controller, component, docker)
        read(controller, [docker, 'pull', candidate], timeout=180)
        module('docker').prepare(container, candidate, compose_files, environment,
                                 output, target='home-core', runner=controller.runner, clock=controller.clock)
    return controller.register(output)
