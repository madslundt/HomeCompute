#!/usr/bin/python3 -I
"""Restricted SSH key dispatcher; cannot register plans or grant approvals."""
import os
from pathlib import Path
import re
import subprocess
import sys
import hashlib
import importlib.util
import json
import time
import uuid

COMMAND = '/usr/local/libexec/homecompute-infrastructure-maintenance-spark'
CONTROLLER = '/opt/homecompute/infrastructure-maintenance/infrastructure_maintenance.py'
FIXES = {'restart-plapre': 'homecompute-plapre-plapre-primary-1',
         'restart-hviske': 'gb10-hviske-primary', 'restart-qwen': 'qwen38-flash-ultrafast'}


def arguments(command):
    for component in ('packages', *FIXES):
        if command == COMMAND + ' prepare ' + component:
            return 'prepare', component
    image = re.fullmatch(re.escape(COMMAND) + r' prepare (image-[A-Za-z0-9][A-Za-z0-9_.-]{0,110})', command)
    if image:
        return 'prepare', image[1]
    approval = re.fullmatch(re.escape(COMMAND) + r' approve ([a-f0-9]{32}) ([a-f0-9]{64})', command)
    if approval:
        return ('approve', *approval.groups())
    match = re.fullmatch(re.escape(COMMAND) + r' (check|run|status|verify) ([a-f0-9]{32})', command)
    if not match:
        raise ValueError('finite plan operation required')
    # check supplies a stable receipt for the parent controller precheck.
    return match.groups()


def full_receipt(controller, plan_id):
    from infrastructure_update_policy import classify_plan
    with controller.connect() as db:
        row = controller.row(db, plan_id)
        plan = json.loads(row['plan'])
    result = controller.status(plan_id)
    result['policy'] = plan['policy']
    result['classification'] = classify_plan(plan['policy'])
    if plan.get('requires_approval') or result['classification']['classification'] == 'unchanged':
        result['classification'].update(approval_required=True, reason='root_plan_requires_approval')
    result['updates_truncated'] = False
    return result


def prepare_component(controller, component):
    spec = importlib.util.spec_from_file_location('spark_preparation', Path(CONTROLLER).with_name('prepare-spark-maintenance.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    if component == 'packages':
        plan, _protected = module.prepare()
    elif component.startswith('image-'):
        from host_maintenance_sources import prepare
        directory = controller.state_dir / 'prepared'
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (uuid.uuid4().hex + '.json')
        prepare(controller, component.removeprefix('image-'), path)
        from infrastructure_maintenance import root_json
        plan = root_json(path, 0)
    elif component in FIXES:
        directory = controller.state_dir / 'prepared'
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (uuid.uuid4().hex + '.json')
        result = subprocess.run(['/usr/local/libexec/homecompute-container-maintenance', 'prepare', FIXES[component], '--output', str(path)],
                                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'HOME': '/root'},
                                capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('fix preparation unavailable')
        from infrastructure_maintenance import root_json
        plan = root_json(path, 0)
    else:
        raise ValueError('unknown Spark component')
    # Reuse an exact live proposal so an owner's approval survives preparation.
    comparable = lambda p: {k: v for k, v in p.items() if k != 'expires_at'}
    with controller.connect() as db:
        rows = db.execute("SELECT * FROM plans WHERE status='registered' ORDER BY rowid DESC LIMIT 32").fetchall()
    for row in rows:
        previous = json.loads(row['plan'])
        if previous['expires_at'] > time.time() + 60 and comparable(previous) == comparable(plan):
            return full_receipt(controller, row['id'])
    directory = controller.state_dir / 'prepared'
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (uuid.uuid4().hex + '.json')
    module.write_plan(path, plan)
    receipt = controller.register(path)
    return full_receipt(controller, receipt['plan_id'])


def main():
    try:
        if os.geteuid() != 0:
            raise ValueError('root forced key required')
        if __import__('socket').gethostname().split('.')[0] != 'home-spark':
            raise ValueError('fixed Spark host required')
        selected = arguments(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
        operation, plan_id = selected[:2]
        sys.path.insert(0, str(Path(CONTROLLER).parent))
        from infrastructure_maintenance import Controller, canonical
        controller = Controller(local_target='home-spark')
        if operation == 'prepare':
            receipt = prepare_component(controller, plan_id)
        elif operation == 'approve':
            controller.approve(plan_id, selected[2])
            receipt = full_receipt(controller, plan_id)
        else:
            if operation == 'run':
                controller.run(plan_id)
            receipt = full_receipt(controller, plan_id)
        if operation in {'run', 'verify'} and receipt.get('status') != 'succeeded':
            raise ValueError('remote update failed or requires approval')
        if operation == 'check':
            receipt.pop('approved', None)
        data = (canonical(receipt) + '\n').encode()
        if len(data) > 65536:
            raise ValueError('bounded receipt exceeded')
        sys.stdout.buffer.write(data)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Spark maintenance request refused.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
