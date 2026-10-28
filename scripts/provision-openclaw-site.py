#!/usr/bin/env python3
"""Add one account mapping without replacing the retained Bitwarden service token."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import termios
from uuid import UUID

from openclaw_bitwarden import provision_account, read_private, validate_config
from openclaw_site_registry import SiteRegistry, account_name

DIRECTORY = Path('/var/lib/homecompute-credential-secrets')
SITES = Path('/var/lib/homecompute-access/config/sites.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--password-secret-id', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not os.isatty(0):
        raise ValueError('Private root terminal required')
    account_name(args.account)
    SiteRegistry.from_file(SITES).profile(args.account)
    if str(UUID(args.password_secret_id)) != args.password_secret_id:
        raise ValueError('Secret reference refused')
    # Project scope/server stay the same; the permanent machine token is reused.
    config = json.loads(read_private(DIRECTORY, 'hotels.json', 4096))
    config['password_secret_id'] = args.password_secret_id
    terminal = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    original = termios.tcgetattr(terminal)
    try:
        hidden = termios.tcgetattr(terminal)
        hidden[3] &= ~termios.ECHO
        termios.tcsetattr(terminal, termios.TCSAFLUSH, hidden)
        username = getpass.getpass('Website sign-in email or username (hidden): ')
        confirmed = getpass.getpass('Confirm sign-in email or username (hidden): ')
        if username != confirmed:
            raise ValueError('Hidden confirmation differs')
        config['username'] = username
        validate_config(config, email_required=False)
        provision_account(DIRECTORY, args.account, config)
    finally:
        termios.tcsetattr(terminal, termios.TCSAFLUSH, original)
        os.close(terminal)
    print(json.dumps({'account_mapping_saved': True, 'existing_connection_reused': True,
                      'credentials_displayed': False, 'browser_access_granted': False}))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt):
        raise SystemExit('Private website provisioning stopped; no credential values are displayed.')
