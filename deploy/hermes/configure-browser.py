"""Configure the fixed, gateway-owned CDP endpoint in Hermes owner profiles."""
import os
from pathlib import Path
import stat
import sys
import tempfile

import yaml


ENDPOINT = 'ws://172.18.0.1:18810/devtools/browser/homecompute'
ENV_KEY = 'BROWSER_CDP_URL='


def replace(path, before, after):
    if before == after:
        return
    if path.is_symlink() or path.read_text() != before:
        raise ValueError(f'{path.name} changed during browser setup')
    mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as staged:
        try:
            staged.write(after)
            staged.flush()
            os.fchmod(staged.fileno(), mode)
            os.fsync(staged.fileno())
            os.replace(staged.name, path)
        finally:
            Path(staged.name).unlink(missing_ok=True)


def profile_changes(home):
    config_path = home / 'config.yaml'
    env_path = home / '.env'
    if home.is_symlink() or any(path.is_symlink() or not path.is_file() for path in (config_path, env_path)):
        raise ValueError('Regular owner profile settings required')
    config_text = config_path.read_text()
    config = yaml.safe_load(config_text) or {}
    browser = config.get('browser') or {}
    if browser.get('cdp_url') not in (None, '', ENDPOINT):
        raise ValueError('Existing browser endpoint differs from the dedicated worker')
    desired = {**browser, 'cdp_url': ENDPOINT, 'allow_private_urls': False}
    desired.pop('backend', None)
    config['browser'] = desired
    config_after = yaml.safe_dump(config, sort_keys=False) if browser != desired else config_text

    env_text = env_path.read_text()
    lines = env_text.splitlines(keepends=True)
    browser_lines = [line for line in lines if line.startswith(ENV_KEY)]
    if any(line.strip() not in (ENV_KEY, ENV_KEY + ENDPOINT) for line in browser_lines):
        raise ValueError('Existing browser environment endpoint differs from the dedicated worker')
    if len(browser_lines) == 1 and browser_lines[0].strip() == ENV_KEY + ENDPOINT:
        env_after = env_text
    else:
        env_after = ''.join(line for line in lines if not line.startswith(ENV_KEY))
        if env_after and not env_after.endswith('\n'):
            env_after += '\n'
        env_after += ENV_KEY + ENDPOINT + '\n'
    return [(config_path, config_text, config_after), (env_path, env_text, env_after)]


def configure(home, profile_names):
    profiles = home / 'profiles'
    if profiles.is_symlink():
        raise ValueError('Regular owner profiles directory required')
    changes = profile_changes(home)
    configured = ['default']
    for name in profile_names:
        profile = profiles / name
        if profile.exists() or profile.is_symlink():
            changes.extend(profile_changes(profile))
            configured.append(name)
    for change in changes:
        replace(*change)
    return configured


if __name__ == '__main__':
    profiles = configure(Path('/sandbox/.hermes'), sys.argv[1:])
    print('Dedicated browser settings verified for ' + ', '.join(profiles))
