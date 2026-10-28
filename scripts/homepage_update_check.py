#!/usr/bin/env python3
"""Finite root operator interface: check Homepage, review and apply exact approvals."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Any

from docker_image_updates import Registry, reference
from homepage_maintenance import CONTAINER, Denied, Maintenance, REPOSITORY

PUBLIC = Path('/var/lib/homecompute-homepage-maintenance-public/status.json')
VERSION = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')


class Checker:
    def __init__(self, executor: Maintenance | None = None, public: Path = PUBLIC,
                 clock: Any = time.time):
        self.executor = executor or Maintenance()
        self.public, self.clock = public, clock

    def publish(self, report: dict[str, Any]) -> dict[str, Any]:
        self.executor.trusted(self.public.parent)
        self.executor.write(self.public, report)
        self.public.chmod(0o644)
        return report

    def project(self, job: dict[str, Any]) -> dict[str, Any]:
        proposal = job['proposal']
        report = {'schema_version': 1, 'checked_at': job['events'][-1]['time'], 'status': job['status'],
                  'candidate_image': proposal['candidate_image'],
                  'release_notes': proposal['review']['release_notes'], 'proposal_id': job['id'],
                  'proposal_hash': job['proposal_hash'], 'expires_at': proposal['expires_at'],
                  'approval_required': True}
        if job['status'] == 'succeeded':
            report['current_image'] = proposal['candidate_image']
        elif job['status'] in {'prepared', 'approved', 'rolled_back', 'failed_before_recreate'}:
            report['current_image'] = proposal['baseline']['image']
        return self.publish(report)

    def check(self) -> dict[str, Any]:
        # Public metadata only; no Docker credential files, release prose or command arguments.
        executor = self.executor
        with executor.locked():
            for path in executor.paths.state.glob('*.json'):
                if re.fullmatch('[a-f0-9]{32}', path.stem):
                    job = executor.load(path.stem)
                    if job['status'] in {'attempt_started', 'recreate_started', 'rollback_started', 'reconciliation_required'}:
                        return self.project(job)
        metadata = executor.inspect('container', CONTAINER)
        current = executor.pin(metadata['Config']['Image'])
        image = executor.inspect('image', metadata['Image'])
        installed = image['Config'].get('Labels', {}).get('org.opencontainers.image.version', '')
        old = VERSION.fullmatch(installed)
        if not old:
            raise Denied('installed Homepage version needs manual review')
        registry = executor.registry
        status, _, body = registry.http('api.github.com', '/repos/gethomepage/homepage/releases/latest',
                                       {'Accept': 'application/vnd.github+json', 'User-Agent': 'HomeCompute-Homepage-Check'}, 524288)
        if status != 200 or len(body) > 524288:
            raise Denied('official release metadata unavailable')
        release = json.loads(body)
        version = release.get('tag_name', '')
        new = VERSION.fullmatch(version) if isinstance(version, str) else None
        url = 'https://github.com/gethomepage/homepage/releases/tag/' + version if new else ''
        if not new or release.get('draft') is not False or release.get('prerelease') is not False or release.get('html_url') != url:
            raise Denied('latest release is not an exact official stable version')
        report = {'schema_version': 1, 'checked_at': self.clock(), 'status': 'checking',
                  'current_image': current, 'current_version': installed, 'candidate_version': version,
                  'release_notes': url, 'approval_required': True}
        old_version, new_version = tuple(map(int, old.groups())), tuple(map(int, new.groups()))
        if new_version < old_version or new_version[0] != old_version[0]:
            report['status'] = 'manual_review_required'
            return self.publish(report)
        platform = registry.platform(reference(REPOSITORY + ':' + version), version,
                                     image['Os'], image['Architecture'], image.get('Variant', ''))
        candidate = REPOSITORY + '@' + platform['index_digest']
        report['candidate_image'] = candidate
        baseline_platform = registry.platform(reference(current), current.split('@')[1], image['Os'], image['Architecture'], image.get('Variant', ''))
        if platform['platform_digest'] == baseline_platform['platform_digest']:
            report['status'] = 'up_to_date'
            return self.publish(report)
        # The executor verifies stateless mounts and health before preparing anything.
        manifest = {'schema_version': 1, 'candidate_image': candidate, 'release_notes': url,
                    'compatibility_review': 'Same major stable release; platform verified. Human must review upstream release notes. No data migration is authorized. Executor verifies read-only stateless configuration before approval.',
                    'rollback_supported': True}
        with executor.locked():
            # Do not mutate a candidate input while an attempt is uncertain.
            for path in executor.paths.state.glob('*.json'):
                if re.fullmatch('[a-f0-9]{32}', path.stem):
                    job = executor.load(path.stem)
                    if job['status'] in {'attempt_started', 'recreate_started', 'rollback_started', 'reconciliation_required'}:
                        raise Denied('previous attempt needs reconciliation')
            executor.trusted(executor.paths.candidate)
            # Fixed bind-mounted file: write under the deployment lock. Partial
            # writes fail closed on recovery; no directory write grant is needed.
            fd = os.open(executor.paths.candidate, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            with os.fdopen(fd, 'w') as handle:
                json.dump(manifest, handle, sort_keys=True)
                handle.flush(); os.fsync(handle.fileno())
        job = executor.prepare()
        if job['proposal']['candidate_image'] != candidate or job['proposal']['review'] != manifest:
            raise Denied('another check changed the reviewed candidate')
        projected = self.project(job)
        projected.update(current_version=installed, candidate_version=version)
        return self.publish(projected)

    def operate(self, operation: str, proposal_id: str, proposal_hash: str | None = None) -> dict[str, Any]:
        if operation in {'approve', 'execute'}:
            job = getattr(self.executor, operation)(proposal_id, proposal_hash)
        else:
            job = self.executor.review(proposal_id)
        self.project(job)
        return job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['check', 'review', 'approve', 'execute', 'status'])
    parser.add_argument('proposal_id', nargs='?')
    parser.add_argument('proposal_hash', nargs='?')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        parser.error('trusted root operator on home-core required')
    checker = Checker()
    try:
        if args.operation == 'check' and args.proposal_id is None and args.proposal_hash is None:
            result = checker.check()
        elif args.operation in {'review', 'status'} and args.proposal_id and args.proposal_hash is None:
            result = checker.operate(args.operation, args.proposal_id)
        elif args.operation in {'approve', 'execute'} and args.proposal_id and args.proposal_hash:
            result = checker.operate(args.operation, args.proposal_id, args.proposal_hash)
        else:
            raise Denied('invalid closed operation arguments')
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        report = {'schema_version': 1, 'checked_at': time.time(), 'status': 'unavailable',
                  'approval_required': True, 'error': 'Check or operation failed; trusted operator review required.'}
        checker.publish(report)
        print(json.dumps(report))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
