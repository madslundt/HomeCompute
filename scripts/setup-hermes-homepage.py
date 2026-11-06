#!/usr/bin/env python3
"""Apply only the canonical Hermes card to the running Homepage config."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import time
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ingress', ROOT / 'scripts/setup-agent-office-ingress.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def patch(current, source):
    cards = re.findall(r'    - Hermes:\n.*?(?=\n    - |\n- |\Z)', source, re.S)
    if len(cards) != 1:
        raise ValueError('canonical-hermes-card-required')
    card = cards[0].rstrip()
    if 'homecore-hermes' in current or '    - Hermes:' in current:
        # Upgrade only the exact original managed Mattermost card. A separate
        # Mattermost card remains the chat entry point.
        legacy = ('    - Hermes:\n        id: homecore-hermes\n'
                  '        icon: mdi-robot-happy-outline\n'
                  '        href: https://mattermost.home.arpa/homecompute/messages/@hermes-agent\n'
                  '        description: Personal Hermes assistant in Mattermost')
        if legacy in current and current.count('    - Hermes:') == 1:
            return current.replace(legacy, card, 1)
        if card not in current:
            raise ValueError('existing-hermes-card-differs')
        return current
    if current.count('- AI assistants:\n') != 1:
        raise ValueError('existing-assistants-group-required')
    return current.replace('- AI assistants:\n', '- AI assistants:\n' + card + '\n\n', 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('root-required')
    core.release(args.revision)
    with ExitStack() as stack:
        for name in ('/var/lib/homecompute/deploy.lock', '/var/lib/homecompute-docker-maintenance/operation.lock'):
            stream = stack.enter_context(open(name, 'a'))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        row = core.inspect(core.HOMEPAGE)
        explicit = [m for m in row['Mounts'] if m['Destination'] == '/app/config/services.yaml']
        path = core.mount(row, '/app/config/services.yaml') if explicit else core.mount(row, '/app/config') / 'services.yaml'
        before = core.protected_file(path)
        after = patch(before.decode(), (ROOT / 'deploy/homepage/config/services.yaml').read_text()).encode()
        report = {'revision': args.revision, 'before': core.sha(before), 'after': core.sha(after), 'applied': False}
        if args.apply:
            if args.expected_sha256 != report['before']:
                raise ValueError('reviewed-config-hash-required')
            if after != before:
                backup = Path('/var/lib/homecompute/hermes-homepage')
                backup.mkdir(mode=0o700, exist_ok=True)
                (backup / (str(time.time_ns()) + '-services.yaml')).write_bytes(before)
                if core.protected_file(path) != before or core.inspect(core.HOMEPAGE)['Id'] != row['Id']:
                    raise ValueError('homepage-baseline-changed')
                try:
                    path.write_bytes(after)
                    core.call(['docker', 'restart', core.HOMEPAGE], timeout=90)
                    from homepage_maintenance import Maintenance
                    for attempt in range(30):
                        try:
                            if Maintenance.http_health():
                                break
                        except OSError:
                            pass
                        time.sleep(2)
                    else:
                        raise ValueError('homepage-health-failed')
                except Exception:
                    path.write_bytes(before)
                    core.call(['docker', 'restart', core.HOMEPAGE], timeout=90)
                    raise
            report['applied'] = True
        print(json.dumps(report))


if __name__ == '__main__':
    main()
