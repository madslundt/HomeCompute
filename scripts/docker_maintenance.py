#!/usr/bin/env python3
"""Root-only Docker replacement from daemon snapshots and verified version tags.

The assistant can execute only private record IDs through the fixed wrapper.
Containers retain their settings, networks and volume identity. A failed attempt
is durably consumed, preserves the former container and restores data on rollback.
"""
from contextlib import contextmanager
import argparse
import copy
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
from urllib.parse import quote, urlencode
import uuid

import docker_maintenance_backup as backup
from docker_image_updates import Registry, reference
from infrastructure_maintenance import canonical, root_json, trusted_path

STATE = Path('/var/lib/homecompute-docker-maintenance')
COMMAND = '/usr/local/libexec/homecompute-docker-maintenance'
ID = re.compile(r'[a-f0-9]{32}\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
IMAGE = re.compile(r'sha256:[a-f0-9]{64}\Z')
VERSION_TAG = re.compile(r'v?(\d+)\.(\d+)\.(\d+)([A-Za-z0-9_.-]*)\Z')
HOSTS = {'home-core', 'home-spark'}


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def selected(value, pattern=ID):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError('invalid finite local identity')
    return value


def tag_version(tag):
    match = VERSION_TAG.fullmatch(tag) if isinstance(tag, str) else None
    if not match or any(word in tag.lower() for word in ('alpha', 'beta', 'rc', 'dev', 'nightly', 'preview')):
        raise ValueError('stable explicit major.minor.patch tag required')
    major, minor, patch, suffix = match.groups()
    return '.'.join(str(int(part)) for part in (major, minor, patch)), suffix


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


class Engine:
    """The only daemon transport is the local fixed Unix socket."""
    def __init__(self):
        version = self.request('GET', '/version', raw=True)
        values = [int(v) for v in version['ApiVersion'].split('.')]
        minimum = tuple(int(v) for v in version.get('MinAPIVersion', '1.41').split('.'))
        chosen = min(tuple(values), (1, 47))
        if chosen < max(minimum, (1, 41)):
            raise ValueError('unsupported Docker API version')
        self.api = '/v' + '.'.join(map(str, chosen))

    def request(self, method, path, body=None, *, raw=False):
        if method not in {'GET', 'POST', 'DELETE'} or not path.startswith('/'):
            raise ValueError('invalid local daemon request')
        connection = UnixConnection('localhost', timeout=45)
        try:
            data = canonical(body).encode() if body is not None else None
            connection.request(method, ('' if raw else self.api) + path, body=data,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = response.read(2 * 1024 * 1024 + 1)
            if response.status not in {200, 201, 204, 304} or len(result) > 2 * 1024 * 1024:
                raise ValueError('local daemon request failed')
            return json.loads(result) if result else None
        finally:
            connection.close()


def endpoint_settings(row):
    return {name: {key: copy.deepcopy(settings[key]) for key in ('IPAMConfig', 'Links', 'Aliases', 'DriverOpts')
                   if settings.get(key) is not None}
            for name, settings in row['NetworkSettings']['Networks'].items()}


def mount_identity(row):
    return sorted((mount['Type'], mount.get('Source', ''), mount['Destination'], bool(mount['RW']))
                  for mount in row['Mounts'])


def clone(row, candidate):
    """Retain settings; make Docker-assigned anonymous volume identities explicit."""
    config, host = copy.deepcopy(row['Config']), copy.deepcopy(row['HostConfig'])
    config['Image'] = candidate
    if host.get('AutoRemove') or host.get('VolumesFrom') or host.get('NetworkMode', '').startswith('container:'):
        raise ValueError('container namespace, auto-remove and inherited volumes need review')
    covered = {item['Target'] for item in host.get('Mounts', [])}
    binds = host.get('Binds') or []
    for item in binds:
        parts = item.split(':')
        if len(parts) not in {2, 3}:
            raise ValueError('ambiguous daemon bind format')
        covered.add(parts[1])
    for mount in row['Mounts']:
        if mount['Type'] == 'volume' and mount['Destination'] not in covered:
            if not NAME.fullmatch(mount.get('Name', '')) or ':' in mount['Destination']:
                raise ValueError('exact volume identity required')
            binds.append(mount['Name'] + ':' + mount['Destination'] + (':rw' if mount['RW'] else ':ro'))
        elif mount['Type'] not in {'bind', 'volume', 'tmpfs'}:
            raise ValueError('unsupported daemon mount needs review')
    if binds:
        host['Binds'] = binds
    return {**config, 'HostConfig': host,
            'NetworkingConfig': {'EndpointsConfig': endpoint_settings(row)}}


def stable(row):
    return {key: row[key] for key in ('Id', 'Name', 'Image', 'Config', 'HostConfig', 'Mounts')} | {
        'networks': endpoint_settings(row),
        'running': row['State']['Running'], 'status': row['State']['Status'],
        'health': row['State'].get('Health', {}).get('Status'),
        'restart_count': row.get('RestartCount', 0)}


def healthy(row):
    state = row['State']
    return (state.get('Running') is True and not any(state.get(k) for k in ('Paused', 'Restarting', 'Dead', 'OOMKilled'))
            and not state.get('Error') and state.get('Health', {}).get('Status') in {None, 'healthy'})


def write(path, value, *, new=False):
    path = Path(path)
    trusted_path(path.parent, 0, directory=True)
    temporary = path if new else path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(canonical(value) + '\n'); stream.flush(); os.fsync(stream.fileno())
        if not new:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if not new:
            temporary.unlink(missing_ok=True)


class Maintenance:
    def __init__(self, host, *, state=STATE, engine=None, registry=None, clock=time.time,
                 sleep=time.sleep, monotonic=time.monotonic):
        if os.geteuid() != 0 or host not in HOSTS:
            raise ValueError('root on a reviewed compute host required')
        self.host, self.state = host, Path(state)
        trusted_path(self.state.parent, 0, directory=True)
        self.state.mkdir(mode=0o700, exist_ok=True)
        trusted_path(self.state, 0, directory=True)
        if stat.S_IMODE(self.state.stat().st_mode) != 0o700:
            raise ValueError('private root Docker maintenance state required')
        self.engine, self.registry = engine or Engine(), registry or Registry()
        self.clock, self.sleep, self.monotonic = clock, sleep, monotonic

    @contextmanager
    def locked(self):
        fd = os.open(self.state / 'operation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def row(self, name):
        selected(name, NAME)
        row = self.engine.request('GET', '/containers/' + quote(name, safe='') + '/json')
        if (not isinstance(row, dict) or not re.fullmatch('[a-f0-9]{64}', row.get('Id', ''))
                or not IMAGE.fullmatch(row.get('Image', '')) or not isinstance(row.get('Mounts'), list)
                or len(row['Mounts']) > 128 or len(canonical(row).encode()) > 200000):
            raise ValueError('one exact daemon container required')
        return row

    def load(self, identity):
        selected(identity)
        record = root_json(self.state / (identity + '.json'), 0)
        if record['id'] != identity or record['host'] != self.host:
            raise ValueError('record host mismatch')
        return record

    def save(self, record):
        write(self.state / (record['id'] + '.json'), record)

    def stage(self, container, candidate_ref, candidate_version, installed_version, *, evidence=None):
        selected(container, NAME)
        candidate = reference(candidate_ref)
        if not candidate['digest'] or not isinstance(evidence, dict) or set(evidence) != {'installed_tag', 'candidate_tag'}:
            raise ValueError('immutable candidate and independently checked stable tags required')
        with self.locked():
            old = self.row(container)
            if old['Name'] != '/' + container:
                raise ValueError('exact existing container name required')
            source = reference(old['Config']['Image'])
            if (source['registry'], source['repository']) != (candidate['registry'], candidate['repository']):
                raise ValueError('candidate must retain registry and repository')
            left, old_suffix = tag_version(evidence['installed_tag'])
            right, new_suffix = tag_version(evidence['candidate_tag'])
            if (left != installed_version or right != candidate_version or old_suffix != new_suffix
                    or left == right):
                raise ValueError('version evidence or image variant mismatch')
            old_image = self.engine.request('GET', '/images/' + quote(old['Image'], safe='') + '/json')
            new_image = self.engine.request('GET', '/images/' + quote(candidate_ref, safe='') + '/json')
            platform = (old_image['Os'], old_image['Architecture'], old_image.get('Variant', ''))
            if platform != (new_image['Os'], new_image['Architecture'], new_image.get('Variant', '')):
                raise ValueError('candidate platform mismatch')
            for tag, image, immutable in ((evidence['installed_tag'], old_image, None),
                                           (evidence['candidate_tag'], new_image, candidate['digest'])):
                proof = self.registry.platform(candidate, tag, *platform)
                if image['Id'] not in set(proof.values()) or immutable and immutable not in proof.values():
                    raise ValueError('stable registry tag does not prove exact image identity')
            sources = sorted({mount['Source'] for mount in old['Mounts']
                              if mount.get('RW') and mount['Type'] != 'tmpfs'})
            reasons = []
            if not healthy(old): reasons.append('unhealthy_or_dormant_baseline')
            payload = clone(old, candidate_ref)
            try:
                backup.scan(sources)
            except (OSError, ValueError):
                reasons.append('backup_requires_review')
            others = self.engine.request('GET', '/containers/json?all=1')
            if not isinstance(others, list) or len(others) > 64:
                raise ValueError('bounded daemon inventory required')
            for other in others:
                if other['Id'] != old['Id'] and other.get('State') == 'running':
                    for mount in self.row(other['Id'])['Mounts']:
                        if mount.get('RW') and mount['Type'] != 'tmpfs' and any(
                                source == mount['Source'] or source.startswith(mount['Source'].rstrip('/') + '/')
                                or mount['Source'].startswith(source.rstrip('/') + '/') for source in sources):
                            reasons.append('shared_writable_data')
            fingerprint = digest([self.host, container, old['Image'], candidate['registry'],
                                  candidate['repository'], candidate['digest']])
            if (self.state / (fingerprint + '.attempt')).exists():
                raise ValueError('this candidate was already attempted; operator reconciliation required')
            identity = uuid.uuid4().hex
            record = {'id': identity, 'host': self.host, 'container': container, 'candidate': candidate_ref,
                      'candidate_image': new_image['Id'], 'installed_version': left, 'candidate_version': right,
                      'old': old, 'payload': payload, 'baseline': digest(stable(old)), 'sources': sources,
                      'fingerprint': fingerprint, 'status': 'prepared', 'reasons': sorted(set(reasons)),
                      'expires_at': self.clock() + 3600}
            write(self.state / (identity + '.json'), record, new=True)
            return identity

    def snapshot(self, identity):
        record = self.load(identity)
        return {'record': identity, 'baseline': digest(stable(self.row(record['container']))),
                'candidate': record['candidate_image'], 'status': record['status']}

    def plan(self, identity):
        record = self.load(identity)
        return {'schema_version': 1, 'policy': {'operation': 'update', 'target': self.host, 'updates': [
            {'component': record['container'], 'scheme': 'semver', 'installed': record['installed_version'],
             'candidate': record['candidate_version']}]}, 'requires_approval': bool(record['reasons']),
            'prechecks': [{'argv': [COMMAND, 'snapshot', identity],
                           'stdout_sha256': hashlib.sha256((canonical(self.snapshot(identity)) + '\n').encode()).hexdigest()}],
            'steps': [[COMMAND, 'apply', identity]], 'postchecks': [[COMMAND, 'verify', identity]],
            'timeout_seconds': 900, 'expires_at': record['expires_at']}

    def network(self, method, container, settings):
        for name, endpoint in settings.items():
            body = {'Container': container, 'EndpointConfig': endpoint} if method == 'connect' else {'Container': container, 'Force': False}
            self.engine.request('POST', '/networks/' + quote(name, safe='') + '/' + method, body)

    def wait(self, name, candidate, *, timeout=120):
        deadline, consecutive = self.monotonic() + timeout, 0
        while True:
            row = self.row(name)
            consecutive = consecutive + 1 if healthy(row) and row['Image'] == candidate and row.get('RestartCount', 0) == 0 else 0
            if consecutive >= (1 if row['State'].get('Health') else 3):
                return row
            if self.monotonic() >= deadline:
                raise ValueError('replacement health was not independently verified')
            self.sleep(5)

    def verify(self, identity):
        record = self.load(identity)
        if record['status'] != 'complete':
            raise ValueError('no completed Docker update to verify')
        row = self.row(record['container'])
        expected = copy.deepcopy(record['old']['Config']); expected['Image'] = record['candidate']
        if (not healthy(row) or row['Image'] != record['candidate_image'] or row['Config'] != expected
                or row['HostConfig'] != record['payload']['HostConfig']
                or mount_identity(row) != mount_identity(record['old'])):
            raise ValueError('updated container identity, config, mounts or health changed')
        actual = endpoint_settings(row)
        for name, endpoint in endpoint_settings(record['old']).items():
            if name not in actual or any(actual[name].get(key) != value for key, value in endpoint.items() if key != 'Aliases'):
                raise ValueError('updated container network configuration changed')
            if not set(endpoint.get('Aliases') or []) <= set(actual[name].get('Aliases') or []):
                raise ValueError('updated container aliases changed')
        return {'verified': True, 'operation_id': identity, 'container': record['container'], 'image': record['candidate']}

    def apply(self, identity):
        with self.locked():
            record = self.load(identity)
            if record['status'] != 'prepared' or self.clock() >= record['expires_at']:
                raise ValueError('expired or consumed Docker update')
            old = self.row(record['container'])
            if digest(stable(old)) != record['baseline'] or not healthy(old):
                raise ValueError('baseline changed, unhealthy or dormant; no deployment attempted')
            if record['reasons']:
                raise ValueError('unsupported baseline requires a separate reviewed adapter')
            backup.scan(record['sources'])
            write(self.state / (record['fingerprint'] + '.attempt'), {'operation_id': identity}, new=True)
            record['status'], record['parked'] = 'attempting', record['container'] + '-before-' + identity[:12]
            self.save(record)  # Durable before the first container mutation.
            archives, stopped, renamed, detached, replacement = [], False, False, {}, None
            try:
                stopped = True  # A timed-out stop may already have stopped the container.
                self.engine.request('POST', '/containers/' + old['Id'] + '/stop?t=30')
                backup.scan(record['sources'])
                for index, source in enumerate(record['sources']):
                    path = self.state / (identity + '-data-' + str(index) + '.tar')
                    backup.create(source, path); archives.append((source, str(path)))
                    if sum(Path(path).stat().st_size for _, path in archives) > backup.MAX_BYTES + backup.MAX_ENTRIES * 2048:
                        raise ValueError('aggregate data backup budget exceeded')
                record['archives'] = archives; self.save(record)
                self.engine.request('POST', '/containers/' + old['Id'] + '/update', {'RestartPolicy': {'Name': 'no'}})
                self.engine.request('POST', '/containers/' + old['Id'] + '/rename?' + urlencode({'name': record['parked']})); renamed = True
                for name, endpoint in endpoint_settings(old).items():
                    self.network('disconnect', old['Id'], {name: endpoint}); detached[name] = endpoint
                created = self.engine.request('POST', '/containers/create?' + urlencode({'name': record['container']}), record['payload'])
                replacement = created['Id']; record['replacement'] = replacement; self.save(record)
                self.engine.request('POST', '/containers/' + replacement + '/start')
                self.wait(record['container'], record['candidate_image'])
                record['status'] = 'complete'; self.save(record)
                result = self.verify(identity)
                write(self.state / (identity + '.active'), {'container': record['container'], 'candidate': record['candidate']}, new=True)
                return result
            except Exception:
                record['status'] = 'uncertain'; self.save(record)
                try:
                    if replacement:
                        self.engine.request('POST', '/containers/' + replacement + '/stop?t=30')
                        self.engine.request('DELETE', '/containers/' + replacement + '?force=1&v=0')
                        for source, path in archives: backup.restore(source, path)
                    if renamed:
                        self.engine.request('POST', '/containers/' + old['Id'] + '/rename?' + urlencode({'name': record['container']}))
                    if detached: self.network('connect', old['Id'], detached)
                    self.engine.request('POST', '/containers/' + old['Id'] + '/update', {'RestartPolicy': old['HostConfig']['RestartPolicy']})
                    if stopped:
                        self.engine.request('POST', '/containers/' + old['Id'] + '/start')
                        self.wait(record['container'], old['Image'])
                    record['status'] = 'rolled_back'; self.save(record)
                except Exception:
                    pass
                raise ValueError('Docker attempt failed; inspect durable record before any further action') from None


def prepare(controller, container, candidate_ref, candidate_version, installed_version, output_path, *, evidence=None):
    if os.geteuid() != 0 or controller.uid != 0:
        raise ValueError('trusted root preparation required')
    helper = Maintenance(controller.local_target)
    identity = helper.stage(container, candidate_ref, candidate_version, installed_version, evidence=evidence)
    plan = helper.plan(identity)
    write(output_path, plan, new=True)
    return {'operation_id': identity, 'plan_file': str(output_path), 'requires_approval': plan['requires_approval']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['snapshot', 'apply', 'verify'])
    parser.add_argument('identity')
    args = parser.parse_args()
    try:
        selected(args.identity)
        helper = Maintenance(socket.gethostname().split('.')[0])
        print(canonical(getattr(helper, args.operation)(args.identity)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        print('Docker maintenance held or uncertain; inspect the private root operation record.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
