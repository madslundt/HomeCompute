"""Trusted-host Bitwarden custody. This module is never an agent tool.

Only an exact configured secret can be retrieved. The caller must be a trusted
site adapter, never a generic tool returning credentials or arbitrary CLI output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import stat
# Execute only a qualified immutable Nix-store CLI, without a shell.
import subprocess  # nosec B404
import tempfile
from uuid import UUID


class CustodyRefused(RuntimeError):
    """A fixed, credential-free diagnostic suitable for operator output."""


def open_directory(path: Path, *, create: bool = False) -> int:
    """Walk absolute paths through held directory descriptors, without symlinks."""
    if not path.is_absolute() or '..' in path.parts:
        raise CustodyRefused('Absolute private directory required')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for index, part in enumerate(path.parts[1:], start=1):
            if create and index == len(path.parts) - 1:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CustodyRefused('Private directory ownership or mode refused')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_private(directory: Path, name: str, maximum: int) -> bytes:
    if not re.fullmatch(r'[a-z][a-z0-9.-]*', name):
        raise CustodyRefused('Private filename refused')
    parent = open_directory(directory)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > maximum):
                raise CustodyRefused('Private file ownership, mode or size refused')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                value = stream.read(maximum + 1)
            if len(value) > maximum:
                raise CustodyRefused('Private file size refused')
            return value
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def create_private(parent: int, name: str, value: bytes) -> None:
    """Publish a complete file without overwriting a concurrent destination."""
    temporary = '.pending-' + secrets.token_hex(16)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
    try:
        with os.fdopen(descriptor, 'wb', closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(descriptor)
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.fsync(parent)
    finally:
        os.close(descriptor)
        os.unlink(temporary, dir_fd=parent)


def validate_config(value: object, *, email_required: bool = True) -> dict:
    expected = {'schema_version', 'server', 'organization_id', 'project_id', 'password_secret_id', 'username'}
    if (not isinstance(value, dict) or set(value) != expected or type(value['schema_version']) is not int
            or value['schema_version'] != 1 or value['server'] != 'https://vault.bitwarden.com'):
        raise CustodyRefused('Credential mapping refused')
    for key in ('organization_id', 'project_id', 'password_secret_id'):
        if not isinstance(value[key], str) or str(UUID(value[key])) != value[key]:
            raise CustodyRefused('Credential mapping refused')
    username = value['username']
    if (not isinstance(username, str) or not 1 <= len(username) <= 320 or email_required and '@' not in username
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in username)):
        raise CustodyRefused('Sign-in email refused')
    return value


def validate_token(token: str) -> None:
    # The pinned bws uses version-0 machine access tokens. Ordinary vault API
    # keys and passwords must not be accepted by this bootstrap prompt.
    pattern = r'0\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9+/=_-]+:[A-Za-z0-9+/=_-]+'
    if not isinstance(token, str) or not 64 <= len(token) <= 4096 or not re.fullmatch(pattern, token):
        raise CustodyRefused('Machine token format refused')


def provision(directory: Path, config: dict, token: str) -> None:
    validate_config(config)
    validate_token(token)
    parent = open_directory(directory, create=True)
    try:
        # Refuse all existing destinations, including symlinks and hard links.
        for name in ('hotels.json', 'bitwarden-access-token'):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise CustodyRefused('Custody already provisioned or incomplete; operator review required')
        create_private(parent, 'hotels.json', json.dumps(config).encode() + b'\n')
        # A failed second write leaves an incomplete mapping, never an active service.
        create_private(parent, 'bitwarden-access-token', token.encode() + b'\n')
    finally:
        os.close(parent)


def provision_account(directory: Path, account: str, config: dict) -> None:
    """Bind another reviewed account to an exact secret, reusing service custody."""
    from openclaw_site_registry import account_name
    account_name(account)
    if account == 'hotels.com':
        raise CustodyRefused('Existing Hotels custody requires operator review')
    validate_config(config, email_required=False)
    validate_token(read_private(directory, 'bitwarden-access-token', 4097).decode().rstrip('\n'))
    parent = open_directory(directory)
    try:
        create_private(parent, 'site-' + account + '.json', json.dumps(config).encode() + b'\n')
    finally:
        os.close(parent)


@dataclass(frozen=True)
class Login:
    username: str = field(repr=False)
    password: str = field(repr=False)


def qualified_bws(executable: Path) -> Path:
    if not executable.is_absolute():
        raise CustodyRefused('Qualified Bitwarden executable required')
    resolved = executable.resolve(strict=True)
    info = resolved.stat()
    if (not re.fullmatch(r'/nix/store/[a-z0-9]{32}-bws-[^/]+/bin/bws', str(resolved))
            or not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID)
            or not info.st_mode & stat.S_IXUSR):
        raise CustodyRefused('Qualified Bitwarden executable required')
    return resolved


class BitwardenLogin:
    """Operator-side exact-ID retrieval; no listing, arbitrary origins or writes."""

    def __init__(self, directory: Path, executable: Path, *, account: str = 'hotels.com'):
        if (not isinstance(account, str) or len(account) > 253
                or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)+', account)):
            raise CustodyRefused('Registered account required')
        self.account = account
        self.directory, self.executable = directory, qualified_bws(executable)

    def retrieve(self) -> Login:
        try:
            filename = 'hotels.json' if self.account == 'hotels.com' else 'site-' + self.account + '.json'
            config = validate_config(json.loads(read_private(self.directory, filename, 4096)),
                                     email_required=self.account == 'hotels.com')
            token = read_private(self.directory, 'bitwarden-access-token', 4097).decode().rstrip('\n')
            validate_token(token)
            # A private, temporary CLI state directory prevents persistent token/session caches.
            # No inherited proxy, tracing, auth, agent or debugging environment is used.
            with tempfile.TemporaryDirectory(prefix='homecompute-bws-') as state:
                # Fixed immutable executable, validated UUID/origin, no shell.
                result = subprocess.run(  # nosec B603
                    [str(self.executable), 'secret', 'get', config['password_secret_id'],
                     '--server-url', config['server'], '--config-file', state + '/config',
                     '--output', 'json', '--color', 'no'],
                    env={'HOME': state, 'XDG_CONFIG_HOME': state, 'BWS_ACCESS_TOKEN': token,
                         'PATH': '/run/current-system/sw/bin:/usr/bin:/bin',
                         'SSL_CERT_FILE': '/etc/ssl/certs/ca-certificates.crt'},
                    cwd=state, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=20, check=False)
            if result.returncode or len(result.stdout) > 65536:
                raise CustodyRefused('Bitwarden retrieval unavailable')
            secret = json.loads(result.stdout)
            if (not isinstance(secret, dict) or secret.get('id') != config['password_secret_id']
                    or secret.get('organizationId') != config['organization_id']
                    or secret.get('projectId') != config['project_id']
                    or secret.get('key') != self.account + '.password'
                    or not isinstance(secret.get('value'), str) or not 1 <= len(secret['value']) <= 4096
                    or '\x00' in secret['value']):
                raise CustodyRefused('Bitwarden credential identity refused')
            return Login(config['username'], secret['value'])
        except Exception:
            # Never chain SDK/CLI errors, their response bodies or secret-bearing arguments.
            raise CustodyRefused('Bitwarden credential retrieval refused') from None
