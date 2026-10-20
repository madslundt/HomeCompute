#!/usr/bin/env python3
"""Prepare an exact Ubuntu package maintenance plan on Spark; install nothing."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable

from infrastructure_maintenance import ENV, MAX_OUTPUT, trusted_path, validate_plan

APT = '/usr/bin/apt-get'
DPKG = '/usr/bin/dpkg-query'
HOSTNAME = '/usr/bin/hostname'
MAX_PACKAGES = 120
PACKAGE_TEXT = r'[a-z0-9][a-z0-9+.-]{0,127}(?::[a-z0-9][a-z0-9-]{0,31})?'
PACKAGE = re.compile(PACKAGE_TEXT + r'\Z')
VERSION = r'[A-Za-z0-9.+:~_-]{1,256}'
INST = re.compile(rf'Inst ({PACKAGE_TEXT}) \[({VERSION})\] \(({VERSION})(?: [^()\r\n]*)?\)(?: \[[^]\r\n]*\])?\Z')
CONF = re.compile(rf'Conf ({PACKAGE_TEXT}) \(({VERSION})(?: [^()\r\n]*)?\)\Z')
SUMMARY = re.compile(r'(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.\Z')
HEADERS = {'The following packages will be upgraded:', 'The following packages have been kept back:',
           'The following upgrades have been deferred due to phasing:',
           'Suggested packages:', 'Recommended packages:'}
PROGRESS = {'Reading package lists...', 'Building dependency tree...', 'Reading state information...',
            'Calculating upgrade...'}
FORMAT = '${Package}\t${Architecture}\t${Version}\t${db:Status-Status}\n'
PROTECTED = re.compile(r'nvidia|cuda|cudnn|nccl|tensorrt|nvinfer|nvrtc|nvjitlink|cublas|cufft|curand|cusolver|cusparse|cupti|nvjpeg|dgx|firmware|ofed|mlnx|ibverbs|rdma|^linux-', re.I)


def parse_simulation(output: bytes) -> list[dict[str, str]]:
    """Account for every effect in the complete solver report; never select a subset."""
    if not isinstance(output, bytes) or not output or len(output) > MAX_OUTPUT:
        raise ValueError('bounded complete apt simulation required')
    rows, configured, summary, listing = {}, set(), None, False
    for line in output.decode('utf-8', errors='strict').splitlines():
        if not line:
            continue
        if line in PROGRESS:
            listing = False
            continue
        if line in HEADERS:
            listing = True
            continue
        words = line.split()
        if (line.startswith('  ') and listing and words and words[0] != '|' and words[-1] != '|'
                and all(PACKAGE.fullmatch(word) or word == '|' for word in words)
                and all(words[index - 1] != '|' for index, word in enumerate(words) if word == '|')):
            continue
        listing = False
        match = SUMMARY.fullmatch(line)
        if match:
            if summary is not None or int(match[2]) or int(match[3]):
                raise ValueError('new packages, removals or repeated summary refused')
            summary = int(match[1])
            continue
        match = INST.fullmatch(line)
        if match:
            name, installed, candidate = match.groups()
            if name in rows or len(rows) >= MAX_PACKAGES or installed == candidate:
                raise ValueError('duplicate, unchanged or unbounded package effects')
            rows[name] = {'component': name, 'scheme': 'debian', 'installed': installed, 'candidate': candidate}
            continue
        match = CONF.fullmatch(line)
        if match:
            name, version = match.groups()
            if name not in rows or name in configured or version != rows[name]['candidate']:
                raise ValueError('unaccounted package configuration')
            configured.add(name)
            continue
        raise ValueError('unrecognized apt solver output or unsupported package effect')
    if summary is None or summary != len(rows) or configured != set(rows) or not rows:
        raise ValueError('incomplete or empty apt upgrade simulation')
    return [rows[name] for name in sorted(rows)]


def expected_versions(output: bytes, updates: list[dict[str, str]]) -> bytes:
    """Bind actual installed architecture/status and compute exact postcheck output."""
    wanted = {row['component']: row for row in updates}
    found, result = set(), []
    if not isinstance(output, bytes) or not output.endswith(b'\n') or len(output) > MAX_OUTPUT:
        raise ValueError('bounded complete dpkg snapshot required')
    for line in output.decode('utf-8', errors='strict').splitlines():
        fields = line.split('\t')
        if len(fields) != 4:
            raise ValueError('invalid dpkg snapshot row')
        name, architecture, installed, status = fields
        if (not PACKAGE.fullmatch(name) or ':' in name or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', architecture)
                or status != 'installed'):
            raise ValueError('unsupported installed package or architecture')
        key = name + ':' + architecture if name + ':' + architecture in wanted else name
        if key not in wanted or key in found or installed != wanted[key]['installed']:
            raise ValueError('apt/dpkg installed snapshot mismatch')
        found.add(key)
        result.append('\t'.join((name, architecture, wanted[key]['candidate'], status)))
    if found != set(wanted):
        raise ValueError('missing dpkg installed package')
    return ('\n'.join(result) + '\n').encode()


def read_command(argv: list[str], runner: Callable[..., Any]) -> bytes:
    result = runner(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                    env=ENV.copy(), cwd='/', shell=False)
    if result.returncode or not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes) or len(result.stdout) + len(result.stderr) > MAX_OUTPUT:
        raise ValueError('trusted read command failed or exceeded output budget')
    return result.stdout


def prepare(*, host: str = 'home-spark', ttl: int = 900,
            runner: Callable[..., Any] = subprocess.run, clock: Callable[[], float] = time.time) -> tuple[dict[str, Any], list[str]]:
    if os.geteuid() != 0:
        raise ValueError('preparation requires root on home-spark')
    if host != 'home-spark' or type(ttl) is not int or not 60 <= ttl <= 3600:
        raise ValueError('fixed Spark host and TTL from 60 to 3600 seconds required')
    if read_command([HOSTNAME, '-s'], runner).strip() != b'home-spark':
        raise ValueError('package preparation must run on home-spark')
    upgrade_argv = [APT, '-s', '--no-remove', 'upgrade']
    upgrade_output = read_command(upgrade_argv, runner)
    updates = parse_simulation(upgrade_output)
    names = [row['component'] for row in updates]
    specs = [row['component'] + '=' + row['candidate'] for row in updates]
    dpkg_argv = [DPKG, '--show', '--showformat=' + FORMAT, *names]
    installed_output = read_command(dpkg_argv, runner)
    target_output = expected_versions(installed_output, updates)
    simulate_argv = [APT, '-s', '--no-remove', '--only-upgrade', 'install', *specs]
    simulate_output = read_command(simulate_argv, runner)
    if parse_simulation(simulate_output) != updates:
        raise ValueError('exact install solver effects differ from upgrade plan')
    protected = [name for name in names if PROTECTED.search(name.split(':')[0])]
    plan = {'schema_version': 1, 'policy': {'operation': 'update', 'target': host, 'updates': updates},
            'prechecks': [{'argv': argv, 'stdout_sha256': hashlib.sha256(output).hexdigest()}
                          for argv, output in [(upgrade_argv, upgrade_output), (dpkg_argv, installed_output), (simulate_argv, simulate_output)]],
            'steps': [[APT, '--yes', '--no-remove', '--only-upgrade', 'install', *specs]],
            'postchecks': [{'argv': dpkg_argv, 'stdout_sha256': hashlib.sha256(target_output).hexdigest()}],
            'timeout_seconds': 900, 'expires_at': clock() + ttl, 'requires_approval': bool(protected)}
    validate_plan(plan, local_target='home-spark')
    return plan, protected


def write_plan(path: Path, plan: dict[str, Any]) -> None:
    if os.geteuid() != 0:
        raise ValueError('root output ownership required')
    path = Path(os.path.abspath(path))
    trusted_path(path.parent, 0, directory=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'w', closefd=False) as stream:
            json.dump(plan, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=['home-spark'], default='home-spark')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ttl', type=int, default=900)
    args = parser.parse_args()
    try:
        plan, protected = prepare(host=args.host, ttl=args.ttl)
        write_plan(args.output, plan)
        classification = validate_plan(plan, local_target='home-spark')
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as error:
        print('Preparation refused: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps({'output': str(args.output.resolve()), 'packages': len(plan['policy']['updates']),
                      'approval_required': classification['approval_required'], 'protected_packages': protected,
                      'reason': 'vendor_sensitive_packages_require_approval' if protected else classification['reason'],
                      'installed_changes': False}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
