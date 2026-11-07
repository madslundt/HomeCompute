#!/usr/bin/env python3
"""Explicitly authorized, source-owned auxiliary LID preparation and CPU lifecycle."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_observer_control', ROOT / 'scripts/language_observer_control.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
A, PIN = C.A, C.PIN


def build(auth: dict, output: Path) -> dict:
    spec = importlib.util.spec_from_file_location('_observer_legacy_build', ROOT / 'scripts/language_observer_build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder.build(auth, output)


def check_only() -> dict:
    catalog = A.catalog()
    C.require({x['name'] for x in catalog if x['group'] == 'model'} ==
              {'embedding_model.ckpt', 'classifier.ckpt', 'label_encoder.txt'}, 'Model asset allowlist differs')
    C.require(sum(x['size'] for x in catalog) <= PIN['download_cap_bytes'], 'Download budget exceeded')
    return {'source_checks': True, 'artifact_count': len(catalog), 'download_bytes': sum(x['size'] for x in catalog),
            'maximum_download_bytes': PIN['download_cap_bytes'], 'model_imports': 0,
            'artifact_requests': 0, 'classification_calls': 0, 'docker_calls': 0,
            'activation_or_production_change': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('check-only', 'fetch', 'verify-artifacts', 'build', 'start', 'guard',
                                            'status', 'job', 'collect', 'stop'))
    parser.add_argument('--authorization', type=Path)
    parser.add_argument('--authorization-sha256')
    parser.add_argument('--source')
    parser.add_argument('--state', type=Path)
    parser.add_argument('--mode', choices=('export', 'verify', 'classify'))
    parser.add_argument('--case')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.operation == 'check-only':
        result = check_only()
    else:
        C.require(args.authorization and re.fullmatch('[a-f0-9]{64}', args.authorization_sha256 or '') and
                  re.fullmatch('[a-f0-9]{40}', args.source or ''), 'Frozen authorization/source required')
        auth = C.authorization(args.authorization, args.authorization_sha256, args.source, args.operation)
        if args.operation in ('fetch', 'verify-artifacts'):
            directory = Path(auth['public_artifacts'])
            if args.operation == 'fetch':
                C.external(str(directory.parent))
                result = A.fetch(directory)
            else:
                result = A.verify(C.external(str(directory)))
        elif args.operation == 'build':
            C.require(args.output, 'New build receipt path required')
            result = build(auth, args.output)
        else:
            C.require(args.state, 'Private lifecycle directory required')
            C.external(str(args.state.parent))
            control = C.Controller(args.state, auth)
            if args.operation == 'start':
                result = control.start(args.mode)
            elif args.operation == 'guard':
                result = control.guard()
            elif args.operation == 'status':
                with control.lock():
                    result = control.read()
                control.inspect(result)
            elif args.operation == 'job':
                C.require(args.mode and args.case, 'Explicit model job mode/ID required')
                raw = b''
                if args.mode == 'classify':
                    C.require(args.input and args.input.is_file() and not args.input.is_symlink(), 'Frozen local WAV required')
                    with args.input.open('rb') as stream:
                        raw = stream.read(PIN['maximum_frames'] * 2 + 45)
                result = control.job(args.mode, args.case, raw)
            elif args.operation == 'collect':
                C.require(args.output, 'New external extraction directory required')
                result = control.collect(args.output)
            else:
                result = control.stop()
    # This channel contains numeric metadata only, never audio/transcripts.
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'failed': True, 'failure_class': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
