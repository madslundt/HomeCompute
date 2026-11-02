#!/usr/bin/env python3
"""Scoped OmniVoice placement test and owner-requested Plapre retirement.

Run only from a clean commit published on origin/master. Docker access is
required; no sudo policy, text/STT service, credential or host release changes.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OLD = {
    'home-spark': ('homecompute-plapre', ('plapre-primary', 'plapre-http', 'plapre-wyoming', 'plapre-edge')),
    'home-core': ('homecompute-control-plane', ('plapre-wyoming-proxy', 'plapre-openai-tts')),
}


def run(arguments, *, timeout=60, capture=True):
    result = subprocess.run(arguments, capture_output=capture, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Managed command failed; inspect locally')
    return result.stdout if capture else ''


def published(revision):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Full published revision required')
    if (run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).strip() != revision
            or run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all']).strip()
            or run(['git', '-C', str(ROOT), 'remote', 'get-url', 'origin']).strip()
            not in {'https://github.com/madslundt/HomeCompute.git', 'git@github.com:madslundt/HomeCompute.git'}):
        raise ValueError('Clean HomeCompute release required')
    run(['git', '-C', str(ROOT), 'fetch', '--quiet', 'origin', 'master'])
    run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', revision, 'origin/master'])


def retired_rows(inspected, host):
    project, services = OLD[host]
    result = []
    for item in inspected:
        labels = item['Config'].get('Labels') or {}
        service = labels.get('com.docker.compose.service')
        name = item['Name'].lstrip('/')
        if (labels.get('com.docker.compose.project') != project or service not in services
                or name != f'{project}-{service}-1'):
            raise ValueError('Plapre container ownership changed; nothing removed')
        result.append({'id': item['Id'], 'name': name, 'image': item['Image'],
                       'service': service, 'running': item['State']['Running']})
    return result


def retire(revision, host, state):
    project, services = OLD[host]
    # Inspect every candidate before the first mutation; no broad Compose down,
    # wildcard removal, volume pruning, image deletion or unknown-project edits.
    identities = []
    for service in services:
        names = run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}',
                     '--filter', f'label=com.docker.compose.service={service}']).split()
        identities.extend(names)
    rows = retired_rows(json.loads(run(['docker', 'inspect', *identities])) if identities else [], host)
    receipt = state / 'plapre-retirement.json'
    if rows:
        if receipt.exists():
            raise ValueError('Existing retirement evidence requires inspection')
        with receipt.open('x') as stream:
            os.chmod(receipt, 0o600)
            json.dump({'revision': revision, 'host': host, 'containers': rows}, stream)
            stream.flush(); os.fsync(stream.fileno())
        for row in rows:
            run(['docker', 'update', '--restart=no', row['id']])
        for row in rows:
            run(['docker', 'stop', '--timeout', '45', row['id']], timeout=60)
            run(['docker', 'rm', row['id']])
    for service in services:
        if run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}',
                '--filter', f'label=com.docker.compose.service={service}']).strip():
            raise ValueError('Plapre retirement readback failed')
    print(json.dumps({'host': host, 'plapre_containers_remaining': 0,
                      'images_and_durable_data_retained': True}))


def benchmark(revision):
    image = 'homecompute/omnivoice:cpu-' + revision
    run(['docker', 'build', '-f', str(ROOT / 'deploy/omnivoice/Dockerfile.cpu'),
         '-t', image, str(ROOT)], timeout=3600, capture=False)
    run(['docker', 'run', '--rm', '--name', 'homecompute-omnivoice-cpu-benchmark',
         '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt',
         'no-new-privileges:true', '--cpus', '4', '--memory', '8g', '--memory-swap', '8g',
         '--pids-limit', '256', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=256m',
         image, '--benchmark'], timeout=1800, capture=False)


def pipeline_candidate(before, engine='tts.piper', voice='da_DK-talesyntese-medium'):
    if before.get('tts_engine') != 'tts.plapre_nano_v2':
        return None
    # Copy every current setting, including concurrent STT/conversation changes.
    after = dict(before)
    identity = after.pop('id')
    after.update(type='assist_pipeline/pipeline/update', pipeline_id=identity,
                 tts_engine=engine, tts_language='da_DK', tts_voice=voice)
    return after


def ha_fallback(state):
    from openclaw_ha_websocket import HomeAssistantSocket
    class TtsMaintenance(HomeAssistantSocket):
        @staticmethod
        def _validate(command):
            kind = command.get('type')
            if command == {'type': 'assist_pipeline/pipeline/list'}:
                return False
            if command == {'type': 'tts/engine/voices', 'engine_id': 'tts.piper', 'language': 'da_DK'}:
                return False
            if (kind == 'assist_pipeline/pipeline/update' and command.get('tts_engine') == 'tts.piper'
                    and command.get('tts_language') == 'da_DK'
                    and command.get('tts_voice') == 'da_DK-talesyntese-medium'):
                return True
            raise ValueError('Only bounded TTS maintenance permitted')
    token_path = Path('/var/lib/homecompute-openclaw/config/household-ha-token')
    token = token_path.read_text().strip()
    with TtsMaintenance(token) as ws:
        voices = ws.call({'type': 'tts/engine/voices', 'engine_id': 'tts.piper', 'language': 'da_DK'})
        if not any(row.get('voice_id') == 'da_DK-talesyntese-medium' for row in voices['voices']):
            raise ValueError('Existing local fallback voice unavailable')
        before = ws.call({'type': 'assist_pipeline/pipeline/list'})
        backup = state / 'ha-tts-before.json'
        if not backup.exists():
            with backup.open('x') as stream:
                os.chmod(backup, 0o600)
                json.dump(before, stream)
                stream.flush(); os.fsync(stream.fileno())
        changed = 0
        for pipeline in before['pipelines']:
            # Refresh before each write, preserving unrelated concurrent changes.
            current = ws.call({'type': 'assist_pipeline/pipeline/list'})
            row = next(p for p in current['pipelines'] if p['id'] == pipeline['id'])
            candidate = pipeline_candidate(row)
            if candidate is None:
                continue
            ws.call(candidate)
            after = ws.call({'type': 'assist_pipeline/pipeline/list'})
            actual = next(p for p in after['pipelines'] if p['id'] == row['id'])
            expected = {k: v for k, v in candidate.items() if k not in {'type', 'pipeline_id'}}
            if actual != {**expected, 'id': row['id']} or after['preferred_pipeline'] != current['preferred_pipeline']:
                raise ValueError('Pipeline readback differs; inspect before retry')
            changed += 1
    print(json.dumps({'home_assistant_tts': 'local-piper-fallback', 'pipelines_changed': changed}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('retire-plapre', 'benchmark-cpu', 'ha-fallback'))
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    if host not in OLD or args.action in {'benchmark-cpu', 'ha-fallback'} and host != 'home-core':
        raise ValueError('Wrong host for this lifecycle')
    published(args.revision)
    state = Path.home() / '.local/state/homecompute/omnivoice'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink() or state.stat().st_uid != os.geteuid() or state.stat().st_mode & 0o077:
        raise ValueError('Private operator state required')
    with (state / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'retire-plapre':
            retire(args.revision, host, state)
        elif args.action == 'ha-fallback':
            ha_fallback(state)
        else:
            benchmark(args.revision)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('OmniVoice lifecycle refused: ' + type(error).__name__) from None
