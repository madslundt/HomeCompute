#!/usr/bin/env python3
"""Print the declared private record, optionally verify it through client DNS."""
import argparse
import json
from pathlib import Path
import socket

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'config/agent-office-dns.json').read_text())
    record, = manifest['records']
    if manifest['schema_version'] != 1 or manifest['zone'] != 'home.arpa' or record != {
        'name': 'agents.home.arpa', 'type': 'A', 'value': '192.168.30.122', 'ttl': 300,
    }:
        raise SystemExit('Private DNS declaration requires review')
    result = {'desired': record, 'verified': False}
    if args.check:
        try:
            observed = sorted({entry[4][0] for entry in socket.getaddrinfo(record['name'], 443, socket.AF_INET, socket.SOCK_STREAM)})
        except OSError:
            observed = []
        result.update(observed=observed, verified=observed == [record['value']])
    print(json.dumps(result))
    return 0 if not args.check or result['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
