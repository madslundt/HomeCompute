#!/usr/bin/env python3
"""Operator-only maintenance review, evidence and approval client."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'status', 'review', 'evidence', 'refresh', 'approve', 'execute', 'cancel'])
    parser.add_argument('action_instance_id', nargs='?')
    parser.add_argument('--token-file', type=Path, default=Path('/run/secrets/openclaw/operator_token'))
    parser.add_argument('--document', type=Path, help='trusted normalized observation envelope; never model output')
    parser.add_argument('--approval-sha256', help='digest returned by review of the exact policy and evidence')
    parser.add_argument('--evidence-id', help='new trusted evidence ID; refresh revokes previous approval')
    args = parser.parse_args()
    body = None
    if args.action in {'list', 'evidence'}:
        if args.action_instance_id:
            parser.error('list/evidence do not take an instance ID')
        path = '/actions'
    else:
        if not args.action_instance_id or not re.fullmatch(r'[0-9a-f-]{36}', args.action_instance_id):
            parser.error('valid action instance UUID required')
        path = '/actions/' + args.action_instance_id
    if args.action == 'evidence':
        if not args.document or args.document.stat().st_size > 131072:
            parser.error('evidence requires --document within the 128 KiB budget')
        body = json.loads(args.document.read_text())
        path += '/evidence'
    elif args.action not in {'list', 'status'}:
        path += '/' + args.action
        if args.action != 'review':
            body = {}
        if args.action == 'approve':
            if not args.approval_sha256 or not re.fullmatch(r'[0-9a-f]{64}', args.approval_sha256):
                parser.error('approve requires --approval-sha256 from review')
            body = {'approval_sha256': args.approval_sha256}
        elif args.action == 'refresh':
            if not args.evidence_id or not re.fullmatch(r'[0-9a-f-]{36}', args.evidence_id):
                parser.error('refresh requires --evidence-id')
            body = {'evidence_id': args.evidence_id}
    request = urllib.request.Request('http://127.0.0.1:18792' + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + args.token_file.read_text().strip(), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        print(json.dumps(json.load(response), indent=2))


if __name__ == '__main__':
    main()
