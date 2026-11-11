#!/usr/bin/env python3
"""Deliver the nightly host update report summary to the operator webhook.

Runs as ExecStartPost of the daily maintenance service. Without a configured
HTTPS webhook URL the summary is only printed to the service journal. Silence
(a fully current report) sends nothing. A refused or failed delivery exits
nonzero so the maintenance unit visibly fails. Report values are trusted only
as bounded public metadata already written by host_maintenance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ATTENTION = {'approval_required', 'review', 'blocked', 'failed', 'uncertain'}
HOST = re.compile(r'home-(?:core|spark)\Z')
TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)\Z')
MAX_REPORT = 1048576
MAX_ITEMS = 80
FIELDS = {'component', 'kind', 'status', 'installed', 'candidate', 'plan_id', 'reason'}

def bounded_text(value):
    return value if isinstance(value, str) and 1 <= len(value) <= 256 else None

def load_report(path):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > MAX_REPORT):
        raise ValueError('report must be a bounded regular root-owned file')
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or not isinstance(value.get('host'), str) or not HOST.fullmatch(value['host'])
            or not isinstance(value.get('generated_at'), str)
            or not TIMESTAMP.fullmatch(value['generated_at'])
            or not isinstance(value.get('items'), list) or len(value['items']) > 4096
            or not isinstance(value.get('summary'), dict)):
        raise ValueError('unsupported host update report')
    return value

def attention_items(items):
    rows = []
    for item in items:
        if not isinstance(item, dict) or item.get('status') not in ATTENTION:
            continue
        row = {key: text for key in sorted(FIELDS & set(item))
               if key != 'status' and (text := bounded_text(item[key]))}
        row['status'] = item['status']
        rows.append(row)
    return rows[:MAX_ITEMS]

def summary_counts(summary):
    return {key: value for key, value in sorted(summary.items())
            if isinstance(key, str) and len(key) <= 64 and isinstance(value, int)}

def post(url, payload):
    body = json.dumps(payload, sort_keys=True).encode()
    request = Request(url, data=body, method='POST',
                      headers={'Content-Type': 'application/json',
                               'User-Agent': 'homecompute-host-update-notify/1'})
    with urlopen(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise ValueError('webhook refused the report receipt: %d' % response.status)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=('home-core', 'home-spark'), required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get('HOMECOMPUTE_HOST_UPDATE_WEBHOOK_URL', '')
    if url and not url.startswith('https://') or url != url.strip() or len(url) > 2048:
        raise ValueError('webhook URL must be a single bounded HTTPS address')
    report = load_report(args.report)
    if report['host'] != args.host:
        raise ValueError('report host does not match the maintenance host')
    rows = attention_items(report['items'])
    summary = summary_counts(report['summary'])
    if not rows:
        print('host update current: %s %s' % (args.host, json.dumps(summary, sort_keys=True)))
        return 0
    if not url:
        print('host update attention: %s %s' % (args.host, json.dumps(summary, sort_keys=True)))
        return 0
    payload = {'type': 'homecompute.host-update', 'host': args.host,
               'generated_at': report['generated_at'], 'summary': summary,
               'attention': rows, 'delivery_at': datetime.now(timezone.utc)
               .isoformat(timespec='seconds').replace('+00:00', 'Z')}
    try:
        post(url, payload)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        print('host update notification delivery failed: %r' % (error,), flush=True)
        return 1
    print('host update notification delivered: %s attention=%d' % (args.host, len(rows)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
