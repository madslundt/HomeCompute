#!/usr/bin/env python3
"""Scoped OmniVoice placement test and owner-requested Plapre retirement.

Run only from a clean commit published on origin/master. Docker access is
required; no sudo policy, text/STT service, credential or host release changes.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OLD = {
    'home-spark': ('homecompute-plapre', ('plapre-primary', 'plapre-http', 'plapre-wyoming', 'plapre-edge')),
    'home-core': ('homecompute-control-plane', ('plapre-wyoming-proxy', 'plapre-openai-tts')),
}


def run(arguments, *, timeout=60, capture=True, environment=None):
    result = subprocess.run(arguments, capture_output=capture, text=True, timeout=timeout, env=environment)
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


def benchmark_gpu(revision):
    image = 'homecompute/omnivoice:gpu-' + revision
    run(['docker', 'build', '-f', str(ROOT / 'deploy/omnivoice/Dockerfile.gpu'),
         '-t', image, str(ROOT)], timeout=3600, capture=False)
    run(['docker', 'run', '--rm', '--name', 'homecompute-omnivoice-gpu-benchmark',
         '--gpus', 'all', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
         '--security-opt', 'no-new-privileges:true', '--cpus', '4', '--memory', '12g',
         '--memory-swap', '12g', '--pids-limit', '256', '--tmpfs',
         '/tmp:rw,noexec,nosuid,nodev,size=512m,uid=65532,gid=65532',
         image, '--benchmark', '--device', 'cuda'], timeout=1800, capture=False)


def deployment_guards():
    # The Docker-authorized operator needs only existence flags from these
    # private receipts. Read-only, no-network, no-capability mounts never expose
    # receipt contents or change host ownership/sudo policy.
    probe_image = 'python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258'
    for directory, pattern in (
        ('/var/lib/homecompute-speech-capacity', 'active.json'),
        ('/var/lib/homecompute-speech-finalization', 'active.json'),
        ('/var/lib/homecompute-docker-maintenance', '*.active'),
    ):
        path = Path(directory)
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_dir():
            raise ValueError('Unexpected maintenance receipt path')
        code = 'from pathlib import Path;print(int(bool(list(Path("/guard").glob(' + repr(pattern) + ')))))'
        active = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                      '--security-opt', 'no-new-privileges:true', '--user', '0:0', '--memory', '64m',
                      '--pids-limit', '32', '--mount', 'type=bind,src=' + directory + ',dst=/guard,readonly',
                      probe_image, 'python3', '-c', code]).strip()
        if active != '0':
            raise ValueError('Reconcile active speech/Docker maintenance through its owning lifecycle first')


def compose(host, revision, *arguments, timeout=1200, capture=True):
    environment = {**os.environ,
        'OMNIVOICE_GPU_IMAGE': 'homecompute/omnivoice:gpu-' + revision,
        'OMNIVOICE_CPU_IMAGE': 'homecompute/omnivoice:cpu-' + revision,
        'OMNIVOICE_ADAPTER_IMAGE': 'homecompute/omnivoice-adapters:' + revision,
        'OMNIVOICE_UPSTREAM_URL': 'http://172.28.200.1:18201',
        'OMNIVOICE_RELEASE_REVISION': revision}
    args = ['docker', 'compose']
    if host == 'home-core':
        args += ['--env-file', '/etc/homecompute/control-plane.env']
    args += ['-f', str(ROOT / ('deploy/omnivoice/compose.core.yaml' if host == 'home-core'
                              else 'deploy/omnivoice/compose.spark.yaml')), *arguments]
    return run(args, environment=environment, timeout=timeout, capture=capture)


def smoke_spark():
    import time
    for language, text in (('da', 'Lyset i køkkenet er nu tændt.'),
                           ('en', 'The kitchen light is now on.')):
        started = time.monotonic()
        request = urllib.request.Request('http://127.0.0.1:8004/v1/audio/speech',
            json.dumps({'text': text, 'speaker': language}).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=120) as response:
            audio = response.read(5760001)
            if (response.status != 200 or response.headers.get('X-Sample-Rate') != '24000'
                    or not 0 < len(audio) <= 5760000 or len(audio) % 2 or not any(audio)):
                raise ValueError('Invalid fixed speech PCM response')
        print(json.dumps({'synthetic_synthesis': 'passed', 'language': language,
                          'seconds': round(time.monotonic()-started, 3), 'audio_seconds': len(audio)/48000}))


def apply_runtime(host, revision):
    deployment_guards()
    if host == 'home-spark':
        available = next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        if available < 8*1024**3:
            raise ValueError('At least 8GiB available memory required; do not shrink another model implicitly')
        compose(host, revision, 'build', timeout=3600, capture=False)
        compose(host, revision, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '600', capture=False)
        smoke_spark()
    else:
        compose(host, revision, 'build', 'omnivoice-wyoming', timeout=1800, capture=False)
        compose(host, revision, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '180',
                'omnivoice-wyoming', 'omnivoice-openai-tts', capture=False)


def reset_failed_networks(revision, state):
    """Recover a failed first start; never stop a running or unrelated container."""
    deployment_guards()
    project = 'homecompute-omnivoice-spark'
    identities = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).split()
    containers = json.loads(run(['docker', 'inspect', *identities])) if identities else []
    rows = []
    for item in containers:
        labels = item['Config'].get('Labels') or {}
        service = labels.get('com.docker.compose.service')
        if (service not in {'omnivoice-engine', 'omnivoice-http'}
                or item['Name'] != '/' + project + '-' + service + '-1' or item['State']['Running']):
            raise ValueError('Network recovery requires only known, stopped OmniVoice containers')
        rows.append({'name': item['Name'], 'id': item['Id'], 'image': item['Image'], 'state': item['State']['Status']})
    names = run(['docker', 'network', 'ls', '--filter', 'label=com.docker.compose.project=' + project,
                 '--format', '{{.Name}}']).split()
    if set(names) - {project + '_edge', project + '_inference'}:
        raise ValueError('Unknown OmniVoice network; preserve for review')
    inspected = json.loads(run(['docker', 'network', 'inspect', *names])) if names else []
    networks = [{'name': n['Name'], 'ipam': n['IPAM'], 'driver': n['Driver']} for n in inspected]
    receipt = state / ('failed-network-recovery-' + revision + '.json')
    with receipt.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump({'revision': revision, 'containers': rows, 'networks': networks}, stream)
        stream.flush(); os.fsync(stream.fileno())
    compose('home-spark', revision, 'down')
    print(json.dumps({'failed_omnivoice_networks_removed': len(names), 'evidence_retained': True}))


def apply_bridge(state):
    """Reconcile the exact tracked predecessor without reinstalling communication."""
    destination = Path('/opt/homecompute/openclaw-communication/scripts/openclaw_speech.py')
    predecessor = run(['git', '-C', str(ROOT), 'show',
        '5c4b046:scripts/openclaw_speech.py']).encode()
    desired = (ROOT / 'scripts/openclaw_speech.py').read_bytes()
    info = destination.lstat()
    before = destination.read_bytes()
    if (destination.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022
            or before not in (predecessor, desired)):
        raise ValueError('Trusted speech module drift requires review')
    if before == desired:
        print(json.dumps({'speech_bridge': 'current'})); return
    backup = state / 'openclaw-speech-before.py'
    if not backup.exists():
        backup.write_bytes(before); backup.chmod(0o600)
    status = run(['systemctl', 'show', 'homecompute-openclaw-adapter.service', '-p', 'ActiveState', '--value']).strip()
    if status not in {'active', 'inactive', 'failed'}:
        raise ValueError('Speech adapter is transitional')
    active = status == 'active'
    if active:
        run(['systemctl', 'stop', 'homecompute-openclaw-adapter.service'])
    temporary = destination.with_suffix('.omnivoice-tmp')
    try:
        with temporary.open('xb') as stream:
            os.fchmod(stream.fileno(), info.st_mode & 0o777)
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            stream.write(desired); stream.flush(); os.fsync(stream.fileno())
        if destination.read_bytes() != before:
            raise ValueError('Speech module changed during cutover')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
        if active:
            run(['systemctl', 'start', 'homecompute-openclaw-adapter.service'])
    if destination.read_bytes() != desired:
        raise ValueError('Speech module readback failed')
    print(json.dumps({'speech_bridge': 'omnivoice', 'sha256': hashlib.sha256(desired).hexdigest()}))


def apply_gateway(revision, state):
    deployment_guards()
    args = ['docker', 'compose', '--env-file', '/etc/homecompute/control-plane.env',
            '-f', str(ROOT / 'deploy/control-plane/compose.yaml')]
    environment = {**os.environ, 'LITELLM_CONFIG_FILE': str(ROOT / 'deploy/control-plane/litellm-config.yaml')}
    desired = json.loads(run([*args, 'config', '--format', 'json'], environment=environment))['services']['litellm']
    container = 'homecompute-control-plane-litellm-1'
    actual = json.loads(run(['docker', 'inspect', container]))[0]
    if (actual['Config']['Image'] != desired['image']
            or actual['Config']['Entrypoint'] != desired['entrypoint']):
        raise ValueError('Gateway image/entrypoint differs; reconcile maintenance first')
    for target, source in (('/etc/litellm/config.yaml', 'deploy/control-plane/litellm-config.yaml'),
                           ('/opt/homecompute/litellm-entrypoint.sh', 'deploy/control-plane/litellm-entrypoint.sh')):
        mounted = next(m['Source'] for m in actual['Mounts'] if m['Destination'] == target)
        before = Path(mounted).read_bytes()
        prior = run(['git', '-C', str(ROOT), 'show', '5c4b046:' + source]).encode()
        if before not in (prior, (ROOT / source).read_bytes()):
            raise ValueError('Gateway source differs; preserve and review drift first')
    receipt = state / 'gateway-before.json'
    if not receipt.exists():
        receipt.write_text(json.dumps({'revision': revision, 'image': actual['Config']['Image'],
            'source_mounts': [{'source': m['Source'], 'destination': m['Destination']}
                for m in actual['Mounts'] if m['Destination'] in {'/etc/litellm/config.yaml', '/opt/homecompute/litellm-entrypoint.sh'}]}))
        receipt.chmod(0o600)
    run([*args, 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'litellm'],
        environment=environment, timeout=240)
    run(['python3', str(ROOT / 'scripts/setup-network-inference.py'), '--enable-omnivoice'])
    print(json.dumps({'gateway_tts': 'omnivoice', 'revision': revision}))


def ha_apply(state):
    """Create OmniVoice through HA's native flow, migrate only TTS, retire old entry."""
    from openclaw_ha_websocket import HomeAssistantSocket, HOST, NoRedirect
    token = Path('/var/lib/homecompute-openclaw/config/household-ha-token').read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def api(path, value=None, method=None):
        request = urllib.request.Request('http://' + HOST + '/api/' + path,
            json.dumps(value).encode() if value is not None else None,
            {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method=method)
        with opener.open(request, timeout=30) as response:
            return json.load(response)
    entries = api('config/config_entries/entry?domain=wyoming')
    old = [entry for entry in entries if entry['title'] == 'plapre-nano-v2']
    current = [entry for entry in entries if entry['title'].lower() == 'omnivoice']
    if len(old) > 1 or len(current) > 1:
        raise ValueError('Ambiguous speech integrations')
    if not current:
        flow = api('config/config_entries/flow', {'handler': 'wyoming', 'show_advanced_options': False})
        result = api('config/config_entries/flow/' + flow['flow_id'],
                     {'host': '192.168.30.122', 'port': 10201})
        if result.get('type') != 'create_entry' or result.get('title', '').lower() != 'omnivoice':
            raise ValueError('Native OmniVoice flow did not complete')
    class Maintenance(HomeAssistantSocket):
        @staticmethod
        def _validate(command):
            if command in ({'type': 'assist_pipeline/pipeline/list'}, {'type': 'tts/engine/list'}):
                return False
            if command == {'type': 'tts/engine/voices', 'engine_id': 'tts.omnivoice', 'language': 'da_DK'}:
                return False
            if (command.get('type') == 'assist_pipeline/pipeline/update'
                    and command.get('tts_engine') == 'tts.omnivoice'
                    and command.get('tts_voice') == 'danish-default' and command.get('tts_language') == 'da_DK'):
                return True
            raise ValueError('Only bounded OmniVoice pipeline migration permitted')
    backup = json.loads((state / 'ha-tts-before.json').read_text())
    selected_ids = {p['id'] for p in backup['pipelines'] if p.get('tts_engine') == 'tts.plapre_nano_v2'}
    with Maintenance(token) as ws:
        voices = ws.call({'type': 'tts/engine/voices', 'engine_id': 'tts.omnivoice', 'language': 'da_DK'})
        if not any(v.get('voice_id') == 'danish-default' for v in voices['voices']):
            raise ValueError('Native Danish OmniVoice unavailable')
        for identity in sorted(selected_ids):
            current = ws.call({'type': 'assist_pipeline/pipeline/list'})
            row = next(p for p in current['pipelines'] if p['id'] == identity)
            if row['tts_engine'] not in {'tts.piper', 'tts.plapre_nano_v2', 'tts.omnivoice'}:
                raise ValueError('Pipeline TTS changed since retirement')
            expected = {**row, 'tts_engine': 'tts.omnivoice', 'tts_language': 'da_DK', 'tts_voice': 'danish-default'}
            command = {k:v for k,v in expected.items() if k != 'id'}
            ws.call({**command, 'type': 'assist_pipeline/pipeline/update', 'pipeline_id': identity})
            after = ws.call({'type': 'assist_pipeline/pipeline/list'})
            if (next(p for p in after['pipelines'] if p['id'] == identity) != expected
                    or after['preferred_pipeline'] != current['preferred_pipeline']):
                raise ValueError('Native pipeline readback failed')
    for entry in old:
        api('config/config_entries/entry/' + entry['entry_id'], method='DELETE')
    print(json.dumps({'home_assistant_tts': 'omnivoice', 'pipelines_migrated': len(selected_ids),
                      'old_integrations_removed': len(old)}))


def pipeline_candidate(before, engine='tts.piper', voice='da_DK-talesyntese-medium'):
    if before.get('tts_engine') not in {'tts.plapre_nano_v2', 'tts.omnivoice'}:
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
    parser.add_argument('action', choices=('retire-plapre', 'benchmark-cpu', 'benchmark-gpu',
                                         'ha-fallback', 'ha-apply', 'apply-bridge', 'apply-gateway',
                                         'reset-failed-networks', 'apply', 'smoke'))
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    if host not in OLD or args.action in {'benchmark-cpu', 'ha-fallback', 'ha-apply', 'apply-bridge', 'apply-gateway'} and host != 'home-core':
        raise ValueError('Wrong host for this lifecycle')
    if args.action in {'benchmark-gpu', 'smoke', 'reset-failed-networks'} and host != 'home-spark':
        raise ValueError('GPU measurement requires home-spark')
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
        elif args.action == 'ha-apply':
            ha_apply(state)
        elif args.action == 'apply-bridge':
            apply_bridge(state)
        elif args.action == 'apply-gateway':
            apply_gateway(args.revision, state)
        elif args.action == 'reset-failed-networks':
            reset_failed_networks(args.revision, state)
        elif args.action == 'benchmark-gpu':
            benchmark_gpu(args.revision)
        elif args.action == 'apply':
            apply_runtime(host, args.revision)
        elif args.action == 'smoke' and host == 'home-spark':
            smoke_spark()
        else:
            benchmark(args.revision)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('OmniVoice lifecycle refused: ' + type(error).__name__) from None
