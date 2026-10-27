"""Root-owned routine maintenance; every package/image lane has an explicit receipt."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid

from infrastructure_maintenance import Controller, ENV, bounded_run, root_json, trusted_path
from host_maintenance_sources import discover

STATE = Path('/var/lib/homecompute-host-maintenance')
PUBLIC = Path('/var/lib/homecompute-host-maintenance-public')
TRACKING = Path('/opt/homecompute/host-maintenance/docker-image-tracking.json')
CONTROLLER = Path('/opt/homecompute/infrastructure-maintenance')
STATUSES = {'current', 'succeeded', 'approval_required', 'review', 'blocked', 'failed', 'uncertain', 'delegated', 'available'}


def write(path, value, mode=0o600):
    path = Path(path)
    trusted_path(path.parent, 0, directory=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    with temporary.open('x') as stream:
        stream.write(json.dumps(value, sort_keys=True) + '\n')
        stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), mode)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def module(name):
    path = CONTROLLER / name
    trusted_path(path, 0)
    spec = importlib.util.spec_from_file_location('host_update_' + name.replace('-', '_'), path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def public_item(item):
    keys = {'component', 'kind', 'status', 'installed', 'candidate', 'plan_id', 'reason'}
    value = {key: item[key] for key in keys if key in item}
    if value.get('status') not in STATUSES:
        raise ValueError('invalid maintenance status')
    if any(not isinstance(text, str) or len(text) > 256 for text in value.values()):
        raise ValueError('bounded public maintenance metadata required')
    return value


def execute(controller, plan, component, kind, attempts, *, check_only=False):
    """Exact controller approval, one-attempt marking, no retry of unknown effects."""
    receipt = controller.register(plan)
    updates = receipt['policy']['updates']
    row = {'component': component, 'kind': kind, 'plan_id': receipt['plan_id']}
    if len(updates) == 1:
        row.update(installed=updates[0]['installed'], candidate=updates[0]['candidate'])
    classification = receipt['classification']
    if classification['approval_required'] or receipt.get('requires_approval'):
        return dict(row, status='approval_required', reason='fresh_exact_plan_requires_approval')
    if classification['classification'] != 'routine':
        return dict(row, status='review', reason='version_review_required')
    if check_only:
        return dict(row, status='available', reason='check_only')
    previous = attempts.get(component, {})
    if previous.get('status') in {'executing', 'failed', 'uncertain'}:
        return dict(row, status='blocked', reason='operator_reconciliation_required')
    attempts[component] = {'status': 'executing', 'plan_id': receipt['plan_id']}
    write(STATE / 'attempts.json', attempts)
    try:
        result = controller.run(receipt['plan_id'])
        status = result['status']
        state = 'succeeded' if status == 'succeeded' else 'uncertain' if status in {'executing', 'uncertain'} else 'failed'
    except Exception:
        state = 'uncertain'
    attempts[component]['status'] = state
    write(STATE / 'attempts.json', attempts)
    return dict(row, status=state, reason='verified_controller_receipt' if state == 'succeeded' else 'operator_reconciliation_required')


def output_path():
    return STATE / ('plan-' + uuid.uuid4().hex + '.json')


def _package_rows(host, controller, attempts, check_only):
    if host == 'home-core':
        if check_only:
            return [{'component': 'nixos', 'kind': 'packages', 'status': 'available', 'reason': 'candidate_build_on_scheduled_run'}]
        if attempts.get('nixos', {}).get('status') in {'executing', 'failed', 'uncertain'}:
            return [{'component': 'nixos', 'kind': 'packages', 'status': 'blocked', 'reason': 'operator_reconciliation_required'}]
        try:
            from core_update_build import prepare
            path = output_path()
            prepare(controller, path)
            row = execute(controller, path, 'nixos', 'packages', attempts)
        except ValueError as error:
            row = {'component': 'nixos', 'kind': 'packages', 'status': 'current' if str(error) == 'no NixOS package updates' else 'failed',
                   'reason': 'no_newer_system' if str(error) == 'no NixOS package updates' else 'preparation_refused'}
        return [row]
    helper, rows = module('prepare-spark-maintenance.py'), []
    if not check_only:
        refreshed = controller.runner(['/usr/bin/apt-get', 'update'], timeout=300, env=ENV.copy(), cwd='/', shell=False)
        if refreshed.returncode:
            return [{'component': 'packages', 'kind': 'packages', 'status': 'failed', 'reason': 'package_metadata_refresh_failed'}]
    try:
        plan, _ = helper.prepare(routine_only=True, runner=controller.runner, clock=controller.clock)
        path = output_path(); helper.write_plan(path, plan)
        rows.append(execute(controller, path, 'packages', 'packages', attempts, check_only=check_only))
    except ValueError as error:
        if str(error) != 'no routine package updates':
            return [{'component': 'packages', 'kind': 'packages', 'status': 'failed', 'reason': 'package_solver_review_required'}]
    # Full remaining solver effects stay visible, including protected NVIDIA/kernel packages.
    try:
        plan, protected = helper.prepare(runner=controller.runner, clock=controller.clock)
        path = output_path(); helper.write_plan(path, plan)
        receipt = controller.register(path)
        rows.append({'component': 'packages-review', 'kind': 'packages', 'status': 'approval_required',
                     'plan_id': receipt['plan_id'], 'reason': 'vendor_or_version_review_required'})
    except ValueError as error:
        if 'empty apt upgrade simulation' in str(error) or str(error) == 'no routine package updates':
            if not rows:
                rows.append({'component': 'packages', 'kind': 'packages', 'status': 'current', 'reason': 'no_package_updates'})
        else:
            rows.append({'component': 'packages-review', 'kind': 'packages', 'status': 'review', 'reason': 'package_solver_review_required'})
    return rows


def package_rows(host, controller, attempts, check_only):
    # Independent package/tool lanes remain visible even when the system
    # package collector, solver, builder, or earlier deployment has failed.
    try:
        rows = _package_rows(host, controller, attempts, check_only)
    except Exception:
        rows = [{'component': 'nixos' if host == 'home-core' else 'packages', 'kind': 'packages',
                 'status': 'failed', 'reason': 'collection_or_preparation_failed'}]
    if host == 'home-core':
        rows.extend([{'component': 'agent-imperative-profile', 'kind': 'packages', 'status': 'review', 'reason': 'imperative_profile_requires_review'},
                     {'component': 'source-pinned-tools', 'kind': 'packages', 'status': 'review', 'reason': 'source_pin_requires_review'}])
    else:
        rows.extend([{'component': 'snap', 'kind': 'packages', 'status': 'delegated', 'reason': 'existing_snap_refresh'},
                     {'component': 'user-python-tools', 'kind': 'packages', 'status': 'review', 'reason': 'user_tool_requires_review'}])
    return rows


def docker_rows(host, controller, attempts, tracking, check_only):
    import docker_maintenance
    rows = []
    for item in discover(host, tracking):
        if item['status'] != 'available':
            rows.append(public_item(item)); continue
        name = item['component']
        if attempts.get(name, {}).get('status') in {'executing', 'failed', 'uncertain'}:
            rows.append({'component': name, 'kind': 'docker', 'status': 'blocked', 'reason': 'operator_reconciliation_required'})
            continue
        if check_only:
            rows.append(public_item(item))
        else:
            try:
                docker = '/run/current-system/sw/bin/docker' if host == 'home-core' else '/usr/bin/docker'
                staged = controller.runner([docker, 'pull', item['candidate_ref']], timeout=300, env=ENV.copy(), cwd='/', shell=False)
                if staged.returncode:
                    raise ValueError('image_pull_failed')
                path = output_path()
                docker_maintenance.prepare(controller, name, item['candidate_ref'], item['candidate'], item['installed'], path,
                                           evidence=item['evidence'])
                rows.append(execute(controller, path, name, 'docker', attempts))
            except Exception:
                rows.append({'component': name, 'kind': 'docker', 'status': 'review', 'reason': 'image_preparation_requires_review',
                             'installed': item['installed'], 'candidate': item['candidate']})
        if item.get('major_candidate'):
            rows.append({'component': name + '.next-major', 'kind': 'docker', 'status': 'approval_required',
                         'installed': item['installed'], 'candidate': item['major_candidate'], 'reason': 'major_release_available'})
    return rows


def run(host, *, check_only=False):
    if os.geteuid() != 0 or host not in {'home-core', 'home-spark'} or socket.gethostname().split('.')[0] != host:
        raise ValueError('root on exact maintenance host required')
    for path, mode in ((STATE, 0o700), (PUBLIC, 0o755)):
        trusted_path(path.parent, 0, directory=True)
        path.mkdir(mode=mode, exist_ok=True); trusted_path(path, 0, directory=True)
    descriptor = os.open(STATE / 'execution.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempts_path = STATE / 'attempts.json'
        attempts = root_json(attempts_path, 0) if attempts_path.exists() else {}
        tracking = root_json(TRACKING, 0)['hosts'][host]
        controller = Controller(local_target=host)
        rows = []
        for operation in (lambda: package_rows(host, controller, attempts, check_only),
                          lambda: docker_rows(host, controller, attempts, tracking, check_only)):
            try:
                rows.extend(operation())
            except Exception:
                rows.append({'component': 'coverage-' + str(len(rows)), 'kind': 'coverage',
                             'status': 'failed', 'reason': 'collection_or_preparation_failed'})
        result = {'schema_version': 1, 'host': host, 'generated_at': datetime.now(timezone.utc).isoformat(),
                  'items': [public_item(row) for row in rows], 'summary': dict(Counter(row['status'] for row in rows))}
        write(PUBLIC / 'report.json', result, 0o644)
        return result
    finally:
        os.close(descriptor)
