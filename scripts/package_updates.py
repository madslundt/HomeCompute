#!/usr/bin/env python3
"""Finite tokenless OS update metadata; disposable indexes, no installations."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
import pwd
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.parse

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.:~_-]{0,160}$")
SUITE = re.compile(r"^(?:/|\./|[a-zA-Z0-9][a-zA-Z0-9_./-]{0,100})$")
SHA = re.compile(r"^[0-9a-f]{40}$")
REASONS = {"none", "unsupported_os", "tool_missing", "source_unavailable", "unsupported_sources",
           "refresh_failed", "deadline_exceeded", "metadata_invalid", "cached_metadata", "stale",
           "flake_unavailable", "upstream_unavailable"}
STATUSES = {"current", "updates_available", "unknown", "stale", "error"}
PUBLIC_HOSTS = {"archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com",
                "developer.download.nvidia.com", "repo.download.nvidia.com", "repo.download.nvidia.cn",
                "packages.nvidia.com", "repo.radeon.com", "download.docker.com", "pkgs.tailscale.com",
                "deb.nodesource.com", "dl.google.com", "packages.microsoft.com", "workbench.download.nvidia.com",
                "snapshot.ppa.launchpadcontent.net"}
ENV = {"PATH": "/run/current-system/sw/bin:/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/nonexistent"}
MAX_ROWS, MAX_OUTPUT = 32, 1024 * 1024
APT_LOCK = threading.Lock()


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty(lane: str, reason: str = "none", status: str = "unknown") -> dict[str, Any]:
    return {"schema_version": 1, "lane": lane, "scope": "installed_packages" if lane == "apt" else "package_catalog",
            "status": status, "collected_at": utc(), "metadata_at": None, "refreshed": False,
            "candidate_count": None, "security_count": None, "security_unknown_count": None,
            "reboot_required": None, "reboot_package_count": None, "candidates": [], "truncated": False,
            "candidate_digest_sha256": None, "coverage_complete": False,
            "eligible_count": None, "phased_count": None, "deferred_count": None,
            "reason": reason, "provenance": {"source": "none", "accepted_sources": 0, "skipped_sources": 0}}


def public_uri(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        return (parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment and parsed.port in (None, 80, 443)
                and not any(ord(c) <= 32 for c in value)
                and (host in PUBLIC_HOSTS or re.fullmatch(r"[a-z]{2}\.archive\.ubuntu\.com", host) is not None))
    except ValueError:
        return False


def public_key(value: str) -> bool:
    path = Path(value)
    return (path.is_absolute() and ".." not in path.parts and not any(c in value for c in '"\\\n\r')
            and any(path.is_relative_to(root) for root in (Path('/usr/share/keyrings'), Path('/etc/apt/keyrings'),
                                                        Path('/etc/apt/trusted.gpg.d')))
            and path.suffix in {".gpg", ".asc"})


def source_lines(text: str, deb822: bool, keys: dict[str, str] | None = None) -> tuple[list[str], int]:
    """Project public signed repositories; never load apt.conf hooks or auth."""
    lines: list[str] = []
    skipped = 0
    if not deb822:
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw or raw.startswith('#') or raw.startswith('deb-src '):
                continue
            match = re.fullmatch(r"deb\s+(?:\[([^\]]+)\]\s+)?(\S+)\s+(\S+)(?:\s+([^#]+))?(?:#.*)?", raw)
            if not match:
                skipped += 1; continue
            options, uri, suite, components = match.groups()
            valid_options = []
            for option in shlex.split(options or ''):
                key, sep, value = option.partition('=')
                if sep and ((key == 'signed-by' and public_key(value))
                            or (key == 'arch' and re.fullmatch(r'[a-z0-9,]+', value))):
                    valid_options.append(option)
                else:
                    valid_options = None; break
            components = (components or '').strip()
            if (not public_uri(uri) or not SUITE.fullmatch(suite) or '..' in suite
                    or not all(TOKEN.fullmatch(x) for x in components.split()) or valid_options is None):
                skipped += 1; continue
            option_text = '[' + ' '.join(valid_options) + '] ' if valid_options else ''
            lines.append(f'deb {option_text}{uri} {suite} {components}'.strip())
        return lines, skipped
    for block in re.split(r'\n\s*\n', text.strip()):
        fields: dict[str, str] = {}
        malformed = False
        previous_key = ''
        for line in block.splitlines():
            if not line.strip() or line.lstrip().startswith('#'): continue
            if line.startswith((' ', '\t')) and previous_key:
                content = line.lstrip()
                if previous_key == 'signed-by': fields[previous_key] += '\n' + ('' if content.strip() == '.' else content)
                else: fields[previous_key] += ' ' + content.strip()
                continue
            key, sep, value = line.partition(':')
            if not sep or key.lower() in fields:
                malformed = True; break
            fields[key.lower()] = value.strip()
            previous_key = key.lower()
        if fields.get('enabled', 'yes').lower() == 'no' or 'deb' not in fields.get('types', '').split(): continue
        allowed = {'types', 'uris', 'suites', 'components', 'signed-by', 'architectures', 'enabled'}
        if malformed or any(key not in allowed and not key.startswith('x-') for key in fields):
            skipped += 1; continue
        options = []
        if 'signed-by' in fields:
            signing = fields['signed-by'].strip()
            if signing.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
                if (len(signing) > 16384 or not re.fullmatch(r'-----BEGIN PGP PUBLIC KEY BLOCK-----\n[A-Za-z0-9+/=\n]+\n-----END PGP PUBLIC KEY BLOCK-----', signing)):
                    skipped += 1; continue
                placeholder = '/usr/share/keyrings/hc-inline-' + hashlib.sha256(signing.encode()).hexdigest() + '.asc'
                if keys is not None: keys[placeholder] = signing + '\n'
                signing = placeholder
            elif not public_key(signing):
                skipped += 1; continue
            options.append('signed-by=' + signing)
        if 'architectures' in fields: options.append('arch=' + ','.join(fields['architectures'].split()))
        prefix = '[' + ' '.join(options) + '] ' if options else ''
        uris, suites = fields.get('uris', '').split(), fields.get('suites', '').split()
        if not uris or not suites or len(uris) * len(suites) > 16:
            skipped += 1; continue
        for uri in uris:
            for suite in suites:
                found, errors = source_lines(f"deb {prefix}{uri} {suite} {fields.get('components', '')}", False)
                lines += found; skipped += errors
    return lines, skipped


def sources(apt_dir: Path = Path('/etc/apt'), keys: dict[str, str] | None = None) -> tuple[list[str], int]:
    paths = [apt_dir / 'sources.list', *sorted((apt_dir / 'sources.list.d').glob('*.list')),
             *sorted((apt_dir / 'sources.list.d').glob('*.sources'))]
    lines, skipped = [], 0
    if len(paths) > 64: raise ValueError('source limit')
    for path in paths:
        if not path.exists(): continue
        if path.is_symlink() or path.stat().st_size > 131072: raise ValueError('source boundary')
        found, count = source_lines(path.read_text(), path.suffix == '.sources', keys)
        lines += found; skipped += count
    if len(lines) > 64: raise ValueError('source limit')
    return sorted(set(lines)), skipped


def copy_preferences(apt_dir: Path, directory: Path) -> int:
    """Copy only bounded root-owned APT pin records, never apt.conf or hooks."""
    parts = apt_dir / 'preferences.d'
    if parts.is_symlink(): raise ValueError('preferences boundary')
    paths = [apt_dir / 'preferences', *sorted(parts.glob('*'))]
    if len(paths) > 65: raise ValueError('preferences budget')
    count = 0
    for path in paths:
        if not path.exists() and not path.is_symlink(): continue
        if path.name != 'preferences' and not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.pref)?', path.name): continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_size > 65536:
                raise ValueError('preferences boundary')
            records = []
            content = source.read(65537)
            if len(content) > 65536: raise ValueError('preferences budget')
            for line in content.splitlines():
                if line.lstrip().startswith('#'): continue
                if not line.strip(): records.append(''); continue
                key, sep, value = line.partition(':')
                if (not sep or key.lower() not in {'package', 'pin', 'pin-priority', 'explanation'}
                        or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                    raise ValueError('preferences record')
                if key.lower() != 'explanation': records.append(line)
            target = directory / ('preferences' if path == apt_dir / 'preferences' else 'preferences.d/' + path.name)
            target.write_text('\n'.join(records) + '\n')
            count += 1
    return count


def apt_config(directory: Path, apt_dir: Path = Path('/etc/apt')) -> Path:
    """APT_CONFIG loads first: isolate configuration, credentials and all writes."""
    for name in ('empty', 'lists', 'lists/partial', 'archives', 'archives/partial', 'preferences.d'):
        (directory / name).mkdir(mode=0o700, exist_ok=True)
    (directory / 'preferences').touch()
    copy_preferences(apt_dir, directory)
    options = {'Dir::Etc::main': '/dev/null', 'Dir::Etc::parts': str(directory / 'empty'),
               'Dir::Etc::sourcelist': str(directory / 'sources.list'), 'Dir::Etc::sourceparts': str(directory / 'empty'),
               'Dir::Etc::preferences': str(directory / 'preferences'), 'Dir::Etc::preferencesparts': str(directory / 'preferences.d'),
               'Dir::Etc::netrc': '/dev/null', 'Dir::Etc::netrcparts': str(directory / 'empty'),
               'Dir::State::status': '/var/lib/dpkg/status', 'Dir::State::lists': str(directory / 'lists'),
               'Dir::State::extended_states': str(directory / 'extended_states'),
               'Dir::Cache::archives': str(directory / 'archives'), 'Dir::Cache::pkgcache': '', 'Dir::Cache::srcpkgcache': '',
               'Dir::Log': str(directory), 'APT::Update::Error-Mode': 'any', 'Acquire::Languages': 'none',
               'Acquire::Retries': '0', 'Acquire::http::Timeout': '10', 'Acquire::https::Timeout': '10',
               'Acquire::http::Proxy': 'DIRECT', 'Acquire::https::Proxy': 'DIRECT',
               'Acquire::http::AllowRedirect': 'false', 'Acquire::https::AllowRedirect': 'false',
               'Acquire::AllowInsecureRepositories': 'false', 'Acquire::AllowDowngradeToInsecureRepositories': 'false',
               'APT::Get::AllowUnauthenticated': 'false', 'APT::Cache-Limit': '536870912'}
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], env=ENV, timeout=5, text=True).strip()
    if not re.fullmatch(r'[a-z0-9-]{1,20}', architecture): raise ValueError('architecture')
    options['APT::Architecture'] = architecture
    config = directory / 'apt.conf'
    config.write_text('\n'.join(f'{key} "{value}";' for key, value in options.items()) + '\n')
    return config


def drop_index_privileges(uid: int, gid: int) -> None:
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    # systemd's narrow ambient SETUID grant leaves an inheritable bit after
    # root -> _apt. Clear every capability before executing the network parser.
    libc = ctypes.CDLL(None, use_errno=True)
    header = (ctypes.c_uint32 * 2)(0x20080522, 0)
    data = (ctypes.c_uint32 * 6)()
    if libc.capset(ctypes.byref(header), ctypes.byref(data)):
        raise OSError(ctypes.get_errno(), 'index reader capability clearing failed')


def refresh(config: Path, *, timeout: int = 120) -> None:
    identity = None
    if os.geteuid() == 0:
        identity = pwd.getpwnam('_apt')
        # Privilege drop applies only to disposable state and this subprocess.
        for path in [config.parent, *config.parent.rglob('*')]:
            os.chown(path, identity.pw_uid, identity.pw_gid, follow_symlinks=False)
    def unprivileged() -> None:
        if identity:
            drop_index_privileges(identity.pw_uid, identity.pw_gid)
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(['apt-get', 'update'], env=dict(ENV, APT_CONFIG=str(config)),
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                                   preexec_fn=unprivileged if identity else None)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                entries = list(config.parent.rglob('*'))
                size = sum(p.stat().st_size for p in entries if p.is_file())
                if time.monotonic() >= deadline: raise TimeoutError('deadline')
                if len(entries) > 2048 or size > 1024 ** 3 or os.fstat(output.fileno()).st_size > MAX_OUTPUT:
                    raise ValueError('refresh budget')
                time.sleep(0.2)
            if process.returncode: raise ValueError('refresh failed')
        finally:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()


def apt_candidates(config: Path | None = None) -> tuple[list[dict[str, Any]], int]:
    # libapt configuration is process-global; isolate initialization and scan.
    with APT_LOCK:
        return _apt_candidates(config)


def eligibility(config: Path, candidates: list[dict[str, Any]]) -> dict[str, int | None]:
    """Read-only native resolver simulation; never force phased updates."""
    result: dict[str, int | None] = dict(eligible_count=None, phased_count=None, deferred_count=None)
    try:
        # APT skips phasing in chroots, including an indeterminate restricted
        # /proc context. Such a simulation cannot establish host eligibility.
        if subprocess.run(['/usr/bin/ischroot'], env=ENV, capture_output=True, timeout=5).returncode != 1: return result
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(['apt-get', '-s', '--no-remove', 'full-upgrade'], env=dict(ENV, APT_CONFIG=str(config)),
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                deadline = time.monotonic() + 30
                while process.poll() is None:
                    if time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > MAX_OUTPUT: return result
                    time.sleep(0.1)
                if process.returncode or os.fstat(output.fileno()).st_size > MAX_OUTPUT: return result
                output.seek(0)
                text = output.read(MAX_OUTPUT).decode('utf-8')
            finally:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait()
        names = {row['name'].split(':')[0] for row in candidates}
        selected = {name.split(':')[0] for name in re.findall(r'^Inst (\S+) ', text, re.M)} & names
        match = re.search(r'^The following upgrades have been deferred due to phasing:\n((?:[ \t]+[^\n]+\n)+)', text, re.M)
        phased = set(match.group(1).split()) & names if match else set()
        result.update(eligible_count=len(selected), deferred_count=len(names - selected), phased_count=len(phased))
    except (OSError, ValueError, subprocess.SubprocessError): pass
    return result


def _apt_candidates(config: Path | None) -> tuple[list[dict[str, Any]], int]:
    import apt_pkg  # Distro python3-apt; absence is explicit, never installed here.
    for key in apt_pkg.config.list(): apt_pkg.config.clear(key)
    previous = os.environ.pop('APT_CONFIG', None)
    if config: os.environ['APT_CONFIG'] = str(config)
    try:
        # Preserve compiled IndexTargets defaults. Clearing AFTER init removes
        # them and silently leaves only dpkg's installed versions in the cache.
        apt_pkg.init_config()
    finally:
        os.environ.pop('APT_CONFIG', None)
        if previous is not None: os.environ['APT_CONFIG'] = previous
    apt_pkg.config['Dir::Cache::pkgcache'] = ''
    apt_pkg.config['Dir::Cache::srcpkgcache'] = ''
    apt_pkg.init_system()
    cache = apt_pkg.Cache(None)
    policy = apt_pkg.DepCache(cache)
    candidates = []
    installed_count = 0
    for package in cache.packages:
        installed = package.current_ver
        if not installed: continue
        installed_count += 1
        if installed_count > 50000: raise ValueError('inventory budget')
        candidate = policy.get_candidate_ver(package)
        if not candidate or apt_pkg.version_compare(candidate.ver_str, installed.ver_str) <= 0: continue
        name = package.get_fullname(True)
        if not all(TOKEN.fullmatch(x) for x in (name, installed.ver_str, candidate.ver_str)):
            raise ValueError('metadata boundary')
        origins = [file for file, _ in candidate.file_list if file.archive != 'now']
        ubuntu = [file for file in origins if file.origin == 'Ubuntu']
        security = any(file.archive.endswith('-security') for file in ubuntu) if ubuntu else None
        candidates.append({'name': name, 'installed': installed.ver_str, 'candidate': candidate.ver_str, 'security': security})
        if len(candidates) > 10000: raise ValueError('inventory budget')
    return sorted(candidates, key=lambda row: row['name']), installed_count


def apt_report(*, fresh: bool = True, apt_dir: Path = Path('/etc/apt'),
               run_dir: Path = Path('/run'), reader: Callable[..., Any] = apt_candidates,
               refresher: Callable[..., Any] = refresh, resolver: Callable[..., Any] = eligibility) -> dict[str, Any]:
    report = empty('apt')
    try:
        if fresh:
            keys: dict[str, str] = {}
            lines, skipped = sources(apt_dir, keys)
            report['provenance'] = {'source': 'isolated_signed_apt_indexes', 'accepted_sources': len(lines), 'skipped_sources': skipped}
            if not lines:
                report.update(reason='source_unavailable'); return report
            with tempfile.TemporaryDirectory(prefix='hc-package-index-') as temporary:
                directory = Path(temporary)
                for index, (placeholder, armor) in enumerate(keys.items()):
                    key = directory / f'public-key-{index}.asc'
                    key.write_text(armor)
                    lines = [line.replace('signed-by=' + placeholder, 'signed-by=' + str(key)) for line in lines]
                (directory / 'sources.list').write_text('\n'.join(lines) + '\n')
                config = apt_config(directory, apt_dir)
                report['provenance']['preference_file_count'] = len(list((directory / 'preferences.d').iterdir())) + int((directory / 'preferences').stat().st_size > 0)
                refresher(config)
                candidates, installed_count = reader(config)
                report.update(resolver(config, candidates))
            report.update(refreshed=True, metadata_at=utc(), status='unknown' if skipped else 'current',
                          reason='unsupported_sources' if skipped else 'none', coverage_complete=not skipped)
        else:
            candidates, installed_count = reader()
            indexes = list(Path('/var/lib/apt/lists').glob('*InRelease'))
            report.update(status='stale', reason='cached_metadata',
                          metadata_at=datetime.fromtimestamp(min(p.stat().st_mtime for p in indexes), timezone.utc).isoformat() if indexes else None)
            report['provenance']['source'] = 'system_apt_indexes'
        report.update(candidate_count=len(candidates), security_count=sum(row['security'] is True for row in candidates),
                      security_unknown_count=sum(row['security'] is None for row in candidates),
                      candidates=candidates[:MAX_ROWS], truncated=len(candidates) > MAX_ROWS)
        report['candidate_digest_sha256'] = hashlib.sha256(json.dumps(candidates, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        report['provenance']['installed_package_count'] = installed_count
        if report['refreshed'] and candidates: report['status'] = 'updates_available'
        report['reboot_required'] = (run_dir / 'reboot-required').exists()
        package_file = run_dir / 'reboot-required.pkgs'
        report['reboot_package_count'] = len(set(package_file.read_text().splitlines())) if package_file.exists() and package_file.stat().st_size <= 65536 else 0
    except ImportError: report.update(status='unknown', reason='tool_missing')
    except TimeoutError: report.update(status='error', reason='deadline_exceeded')
    except (OSError, ValueError, SystemError, TypeError, AttributeError, subprocess.SubprocessError):
        report.update(status='error', reason='refresh_failed' if fresh else 'metadata_invalid')
    return report


def nix_report(release: Path = Path('/srv/homecompute/current'),
               runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    report = empty('nixos-flake')
    try:
        lock = release / 'flake.lock'
        if not lock.is_file() or lock.stat().st_size > MAX_OUTPUT: raise ValueError('lock missing')
        document = json.loads(lock.read_text())
        nodes = document['nodes']
        input_name = nodes[document.get('root', 'root')]['inputs']['nixpkgs']
        node = nodes[input_name]
        original, locked = node['original'], node['locked']
        branch = original.get('ref')
        if (any(record.get('type') != 'github' or record.get('owner') != 'NixOS' or record.get('repo') != 'nixpkgs'
                for record in (original, locked))
                or not isinstance(branch, str) or not re.fullmatch(r'nixos-[0-9]{2}\.[0-9]{2}', branch)
                or not SHA.fullmatch(locked.get('rev', ''))): raise ValueError('unsupported flake input')
        result = runner(['git', '-c', 'credential.helper=', '-c', 'core.hooksPath=/dev/null', '-c', 'http.followRedirects=false',
                         'ls-remote', 'https://github.com/NixOS/nixpkgs.git', 'refs/heads/' + branch],
                        env=dict(ENV, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0'),
                        cwd='/', capture_output=True, text=True, timeout=15, check=False)
        if result.returncode or len(result.stdout) > 4096: report.update(status='error', reason='upstream_unavailable'); return report
        head, ref = result.stdout.strip().split('\t')
        if not SHA.fullmatch(head) or ref != 'refs/heads/' + branch: raise ValueError('upstream metadata')
        changed = locked['rev'] != head
        report.update(status='updates_available' if changed else 'current', refreshed=True, metadata_at=utc(),
                      coverage_complete=True,
                      candidate_count=int(changed), candidates=[{'name': 'nixpkgs', 'installed': locked['rev'], 'candidate': head, 'security': None}] if changed else [])
        report['candidate_digest_sha256'] = hashlib.sha256(json.dumps(report['candidates'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        report['provenance'] = {'source': 'deployed_flake_lock_and_public_branch', 'accepted_sources': 1,
                                'skipped_sources': 0, 'branch': branch}
    except subprocess.TimeoutExpired: report.update(status='error', reason='deadline_exceeded')
    except OSError: report.update(reason='tool_missing')
    except (ValueError, TypeError, KeyError, json.JSONDecodeError): report.update(reason='flake_unavailable')
    return report


def project_report(value: Any, now: datetime, ttl: int = 86400) -> dict[str, Any]:
    """Validate untrusted remote metadata and return a safe bounded projection."""
    result = empty('apt', 'metadata_invalid')
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('lane') not in {'apt', 'nixos-flake'}: return result
    result = empty(value['lane'], 'metadata_invalid')
    try:
        if value['status'] not in STATUSES or value['reason'] not in REASONS: raise ValueError('status')
        stamp = datetime.fromisoformat(value['collected_at'])
        if stamp.tzinfo is None or (now - stamp).total_seconds() < -60: raise ValueError('timestamp')
        for field in ('candidate_count', 'security_count', 'security_unknown_count', 'reboot_package_count', 'eligible_count', 'phased_count', 'deferred_count'):
            number = value.get(field)
            if number is not None and (type(number) is not int or not 0 <= number <= 50000): raise ValueError('count')
            result[field] = number
        for field in ('eligible_count', 'phased_count', 'deferred_count'):
            if result[field] is not None and (result['candidate_count'] is None or result[field] > result['candidate_count']): raise ValueError('eligibility count')
        for field in ('refreshed', 'truncated', 'coverage_complete'):
            if type(value[field]) is not bool: raise ValueError('boolean')
            result[field] = value[field]
        if value['reboot_required'] is not None and type(value['reboot_required']) is not bool: raise ValueError('reboot')
        result['reboot_required'] = value['reboot_required']
        digest = value['candidate_digest_sha256']
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)): raise ValueError('digest')
        result['candidate_digest_sha256'] = digest
        if not isinstance(value['candidates'], list) or len(value['candidates']) > MAX_ROWS: raise ValueError('rows')
        for row in value['candidates']:
            if (not isinstance(row, dict) or set(row) != {'name', 'installed', 'candidate', 'security'}
                    or not all(isinstance(row[key], str) and TOKEN.fullmatch(row[key]) for key in ('name', 'installed', 'candidate'))
                    or row['security'] is not None and type(row['security']) is not bool): raise ValueError('row')
        result.update(candidates=value['candidates'], status=value['status'], reason=value['reason'],
                      collected_at=stamp.isoformat())
        if value['metadata_at'] is not None:
            metadata = datetime.fromisoformat(value['metadata_at'])
            if metadata.tzinfo is None or (now - metadata).total_seconds() < -60: raise ValueError('metadata time')
            result['metadata_at'] = metadata.isoformat()
        provenance = value['provenance']
        if provenance['source'] not in {'none', 'isolated_signed_apt_indexes', 'system_apt_indexes', 'deployed_flake_lock_and_public_branch'}: raise ValueError('source')
        result['provenance']['source'] = provenance['source']
        for key in ('accepted_sources', 'skipped_sources', 'installed_package_count', 'preference_file_count'):
            count = provenance.get(key, 0)
            if type(count) is not int or not 0 <= count <= 50000: raise ValueError('source count')
            result['provenance'][key] = count
        if 'branch' in provenance and re.fullmatch(r'nixos-[0-9]{2}\.[0-9]{2}', provenance['branch']): result['provenance']['branch'] = provenance['branch']
        if value['status'] in {'current', 'updates_available'} and (not result['refreshed'] or not result['metadata_at']): raise ValueError('freshness claim')
        if value['status'] == 'current' and (not result['coverage_complete'] or result['candidate_count'] != 0): raise ValueError('coverage claim')
        if (now - stamp).total_seconds() > ttl or result['metadata_at'] and (now - datetime.fromisoformat(result['metadata_at'])).total_seconds() > ttl:
            result.update(status='stale', reason='stale')
        return result
    except (ValueError, TypeError, KeyError): return empty(value['lane'], 'metadata_invalid')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lane', choices=('auto', 'apt', 'nixos-flake'), default='auto')
    parser.add_argument('--cached', action='store_true', help='APT cache only: always advisory/stale')
    args = parser.parse_args()
    release = Path('/etc/os-release').read_text()
    lane = args.lane
    if lane == 'auto': lane = 'apt' if re.search(r'^ID=ubuntu$', release, re.M) else 'nixos-flake' if re.search(r'^ID=nixos$', release, re.M) else 'unsupported'
    report = apt_report(fresh=not args.cached) if lane == 'apt' else nix_report() if lane == 'nixos-flake' else empty('apt', 'unsupported_os')
    print(json.dumps(project_report(report, datetime.now(timezone.utc)), separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
