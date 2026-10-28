#!/usr/bin/env python3
"""Operator-only, hidden-input Bitwarden token intake. Does not start a service."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import termios

from openclaw_bitwarden import CustodyRefused, open_directory, provision

DESTINATION = Path('/var/lib/homecompute-credential-secrets')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--organization-id', required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--password-secret-id', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise CustodyRefused('Run as root on the trusted core host')
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise CustodyRefused('An interactive operator terminal is required')
    # Refuse before accepting secret input when the destination is already used.
    parent = open_directory(DESTINATION, create=True)
    try:
        for name in ('hotels.json', 'bitwarden-access-token'):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise CustodyRefused('Existing custody files require operator review; nothing overwritten')
    finally:
        os.close(parent)
    config = {'schema_version': 1, 'server': 'https://vault.bitwarden.com',
              'organization_id': args.organization_id, 'project_id': args.project_id,
              'password_secret_id': args.password_secret_id}
    # Disable echo explicitly before getpass: no insecure getpass fallback is allowed.
    # getpass opens its own read handle. The prompt stream only needs writes:
    # buffered update mode (r+) requires seeking and fails on Linux terminals.
    with open('/dev/tty', 'w') as terminal:
        saved = termios.tcgetattr(terminal.fileno())
        hidden = saved.copy()
        hidden[3] &= ~(termios.ECHO | termios.ECHONL)
        try:
            termios.tcsetattr(terminal.fileno(), termios.TCSAFLUSH, hidden)
            config['username'] = getpass.getpass('Hotels.com sign-in email (hidden): ', stream=terminal)
            token = getpass.getpass('Bitwarden MACHINE access token (hidden): ', stream=terminal)
            confirmation = getpass.getpass('Paste the same token again (hidden): ', stream=terminal)
        finally:
            termios.tcsetattr(terminal.fileno(), termios.TCSAFLUSH, saved)
    if token != confirmation:
        raise CustodyRefused('Token entries differ; no credential files were created')
    provision(DESTINATION, config, token)
    del token, confirmation
    print(json.dumps({'stored': True, 'owner': 'root', 'directory_mode': '0700',
                      'file_mode': '0600', 'service_started': False, 'agent_access': False}))


if __name__ == '__main__':
    try:
        main()
    except CustodyRefused as error:
        # CustodyRefused diagnostics are fixed strings defined by our code.
        print('Bitwarden provisioning stopped: ' + str(error) + '. '
              'No credential values are displayed.', file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('Bitwarden provisioning cancelled; no credential values are displayed.', file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('Bitwarden provisioning stopped unexpectedly; no credential values are displayed. '
              'Operator review is required before retrying.', file=sys.stderr)
        raise SystemExit(1)
