"""Inspect or import fresh Desktop bot profiles using the qualified Hermes runtime."""

import fcntl
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile

KEY = 'HOMECOMPUTE_HERMES_BOT_KEY'
MANAGED_FILES = ('config.yaml', 'profile.yaml', 'SOUL.md', '.no-bundled-skills')
# The installed gateway can add plugin and platform toolsets to an explicit list.
DISABLED_TOOLSETS = ['audio', 'bighelp', 'bighelp_templates', 'bighelp_workflows',
                     'conduit', 'conduit_rooms', 'feishu_doc', 'feishu_drive',
                     'nemoclaw', 'omh', 'omh-lsp']


def json_file(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def profile_files(bot, endpoint, platforms):
    return {
        'config.yaml': json_file({
            'model': {'default': bot['model'], 'provider': 'custom',
                      'base_url': endpoint, 'key_env': KEY},
            'agent': {'reasoning_effort': bot['reasoning_effort'], 'coding_context': 'off',
                      'disabled_toolsets': DISABLED_TOOLSETS},
            'fallback_providers': [],
            'platform_toolsets': {platform: ['memory'] for platform in platforms},
            'mcp_servers': {},
            'plugins': {'enabled': []},
            'telemetry': {'shared_metrics': {'enabled': False, 'send': False, 'offer_version': 2}},
        }),
        'profile.yaml': json_file({
            'display_name': bot['name'],
            'description': bot['description'],
            'ui_meta': {'hermes-bots': {'title': bot['name']}},
        }),
        'SOUL.md': bot['soul'].encode(),
        '.no-bundled-skills': b'Managed HomeCompute specialist\n',
    }


def private_file(path):
    info = path.lstat()
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and not info.st_mode & 0o077 and info.st_size <= 16384)


def inspect(path, files, load_config, key=None):
    if path.is_symlink():
        return 'conflict'
    if not path.exists():
        return 'absent'
    if not path.is_dir():
        return 'conflict'
    if any(not (path / name).exists() or not private_file(path / name)
           for name in (*MANAGED_FILES, '.env')):
        return 'conflict'
    for name in MANAGED_FILES:
        actual = (path / name).read_bytes()
        if name.endswith('.yaml'):
            if load_config(actual) != json.loads(files[name]):
                return 'conflict'
        elif actual != files[name]:
            return 'conflict'
    credential = (path / '.env').read_text()
    line = credential.removesuffix('\n')
    if (not credential.endswith('\n') or not line.startswith(f'{KEY}=sk-')
            or any(character.isspace() for character in line)):
        return 'conflict'
    if key is not None and line != f'{KEY}={key}':
        return 'conflict'
    return 'managed'


def import_fresh(profiles, name, files, key, import_profile):
    # Package only generated files. The owner's environment, tools and state
    # are never read. Native import uses a private extraction directory.
    with tempfile.NamedTemporaryFile(dir=profiles.parent, suffix='.tar.gz') as archive:
        with tarfile.open(fileobj=archive, mode='w:gz') as package:
            for filename, contents in {**files, '.env': f'{KEY}={key}\n'.encode()}.items():
                entry = tarfile.TarInfo(f'{name}/{filename}')
                entry.mode = 0o600
                entry.size = len(contents)
                package.addfile(entry, io.BytesIO(contents))
        archive.flush()
        import_profile(archive.name, name=name)


def reconcile(profiles, payload, platforms, load_config, import_profile, is_deleted):
    keys = payload.get('keys', {})
    entries = {bot['id']: profile_files(bot, payload['endpoint'], platforms) for bot in payload['bots']}

    def readback():
        result = {}
        for name, files in entries.items():
            state = inspect(profiles / name, files, load_config, keys.get(name))
            if state == 'conflict' or is_deleted(profiles / name):
                raise ValueError(f'existing {name} requires review')
            result[name] = state
        return result

    observed = readback()
    if keys:
        if observed != payload['expected']:
            raise ValueError('bot profile state changed since the reviewed plan')
        for name, files in entries.items():
            if observed[name] == 'absent':
                import_fresh(profiles, name, files, keys[name], import_profile)
        observed = readback()
        if set(observed.values()) != {'managed'}:
            raise ValueError('native profile readback differs')
    return observed


def main():
    from hermes_cli.profiles import get_profile_dir, import_profile, named_profile_is_deleted
    from hermes_cli.platforms import PLATFORMS
    from hermes_cli.tools_config import _get_platform_tools
    import yaml

    payload = json.load(sys.stdin)
    profiles = Path('/sandbox/.hermes/profiles')
    if (get_profile_dir(payload['bots'][0]['id']).parent != profiles
            or any(path.is_symlink() for path in (profiles, *profiles.parents))):
        raise ValueError('unexpected owner profile location')
    os.umask(0o077)
    platforms = sorted(set(PLATFORMS) | {'desktop', 'webapp'})
    for bot in payload['bots']:
        config = json.loads(profile_files(bot, payload['endpoint'], platforms)['config.yaml'])
        if any(_get_platform_tools(config, platform) != {'memory'} for platform in platforms):
            raise ValueError('Hermes resolver returned an unexpected specialist toolset')
    arguments = (profiles, payload, platforms, yaml.safe_load, import_profile, named_profile_is_deleted)
    if 'keys' in payload:
        # Keep native extraction and the final profile on the same filesystem.
        tempfile.tempdir = str(profiles.parent)
        lock_path = profiles.parent / '.homecompute-desktop-bots.lock'
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = reconcile(*arguments)
    else:
        result = reconcile(*arguments)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
