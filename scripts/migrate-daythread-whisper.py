#!/usr/bin/env python3
"""Migrate only Daythread STT selection through its revision-checked settings API.

Run inside the existing backend container. Credentials, audio, transcripts,
summaries and unrelated settings are neither returned nor changed.
"""
from __future__ import annotations
import argparse
import json
import urllib.request

ENDPOINT = 'http://127.0.0.1:8787/v1/configuration'
ALIASES = {'whisper-turbo', 'stt-danish', 'whisper'}


def changes(configuration):
    values = configuration['values']
    if values['stt_model'] not in ALIASES:
        raise ValueError('Unexpected STT model; review consumer configuration')
    for language, route in values['stt_routes'].items():
        if (language not in {'da', 'da-DK'} or route.get('model') not in ALIASES or
                route.get('base_url') not in {None, '', values['stt_base_url']} or
                configuration['secrets'].get('stt_routes.' + language + '.token')):
            raise ValueError('Unreviewed STT override; nothing changed')
    return {'stt_model': 'whisper', 'stt_routes': {}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with urllib.request.urlopen(ENDPOINT, timeout=30) as response:
        before = json.load(response)
    selected = changes(before)
    if args.apply and any(before['values'][k] != v for k, v in selected.items()):
        request = urllib.request.Request(ENDPOINT,
            json.dumps({'revision': before['revision'], 'changes': selected}).encode(),
            {'Content-Type': 'application/json'}, method='PATCH')
        with urllib.request.urlopen(request, timeout=30) as response:
            after = json.load(response)
        if any(after['values'][k] != v for k, v in selected.items()):
            raise ValueError('STT settings readback failed')
    print(json.dumps({'apply': args.apply, 'stt_model': 'whisper', 'language_overrides': 0}))


if __name__ == '__main__':
    try: main()
    except Exception:
        raise SystemExit('Daythread STT migration failed; inspect locally without exposing settings') from None
