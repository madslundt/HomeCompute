#!/usr/bin/env python3
"""Move Wyoming to core from a published release without restarting GPU models.

Commands are deliberately staged: spark-prepare, core-stage, core-apply,
verify, spark-retire. core-rollback restores the saved pre-migration adapters.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
import time

SOURCE = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute/speech-boundary')
INSTALL = Path('/opt/homecompute/speech-boundary')
UNITS = Path('/etc/systemd/system.attached')
DROPIN = UNITS / 'homecompute-compute-ssh-tunnel.service.d/40-homecompute-speech-boundary.conf'
CORE_ENV = Path('/etc/homecompute/control-plane.env')
CORE_SERVICES = ('plapre-wyoming-proxy', 'hviske-wyoming-proxy')
SPARK_LEGACY = ('homecompute-plapre-plapre-edge-1', 'homecompute-plapre-plapre-wyoming-1', 'gb10-hviske-wyoming')
BIN = '/run/current-system/sw/bin'


def run(args: list[str], *, output: bool = False) -> str:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode:
        # Never relay Docker inspect/env output or HTTP response bodies.
        raise RuntimeError(f'{args[0]} {args[1] if len(args)>1 else ""} failed (exit {result.returncode})')
    return result.stdout if output else ''


def trusted(path: Path, *, directory: bool = False) -> None:
    info = path.stat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError(f'Unsafe managed path: {path}')
    if directory and not path.is_dir():
        raise ValueError('Expected managed directory')


def write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    trusted(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        trusted(path)
    temporary = path.with_name(path.name + '.install-tmp')
    with temporary.open('x') as stream:
        os.chmod(temporary, mode)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def release_guard(revision: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('An exact published release SHA is required')
    if SOURCE != Path('/srv/homecompute/releases') / revision:
        raise ValueError('Run the installer from the selected immutable release directory')
    trusted(SOURCE, directory=True)
    if run(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], output=True).strip() != revision:
        raise ValueError('Release revision mismatch')
    if run(['git', '-C', str(SOURCE), 'status', '--porcelain', '--untracked-files=all'], output=True).strip():
        raise ValueError('Release contains tracked modifications')
    run(['git', '-C', str(SOURCE), 'merge-base', '--is-ancestor', revision, 'origin/master'])


def tunnel_patch(source: str) -> str:
    # Preserve the existing admission-gated LLM route if this host's Nix
    # generation predates it. All unrelated forwards and SSH flags stay intact.
    for old, new in (
        (':18005:127.0.0.1:18300', ':18005:127.0.0.1:18400'),
        (':18201:127.0.0.1:10201', ':18201:127.0.0.1:8004'),
        (':18301:127.0.0.1:10301', ':18301:127.0.0.1:8003'),
    ):
        counts = source.count(old), source.count(new)
        if counts == (1, 0):
            source = source.replace(old, new, 1)
        elif counts != (0, 1):
            raise ValueError('Unsupported or ambiguous inference tunnel layout')
    return source


def base_runner() -> tuple[Path, str]:
    fragment = Path(run(['systemctl', 'show', 'homecompute-compute-ssh-tunnel.service',
                         '-p', 'FragmentPath', '--value'], output=True).strip()).resolve(strict=True)
    if not fragment.is_relative_to('/nix/store'):
        raise ValueError('Expected immutable Nix tunnel unit')
    trusted(fragment)
    starts = re.findall(r'^ExecStart=(.*)$', fragment.read_text(), re.M)
    if len(starts) != 1 or not re.fullmatch(r'/nix/store/[a-z0-9]{32}-homecompute-compute-ssh-tunnel/bin/homecompute-compute-ssh-tunnel', starts[0]):
        raise ValueError('Expected fixed Nix tunnel runner')
    runner = Path(starts[0]).resolve(strict=True)
    trusted(runner)
    return runner, runner.read_text()


def legacy_tunnel_patch(source: str) -> str:
    source = tunnel_patch(source)
    return source.replace(':18201:127.0.0.1:8004', ':18201:127.0.0.1:10201', 1).replace(
        ':18301:127.0.0.1:8003', ':18301:127.0.0.1:10301', 1)


def run_tunnel(legacy: bool = False) -> None:
    runner, source = base_runner()
    patched = legacy_tunnel_patch(source) if legacy else tunnel_patch(source)
    interpreter = patched.splitlines()[0].removeprefix('#!')
    if not re.fullmatch(r'/nix/store/[a-z0-9]{32}-bash-[^/]+/bin/bash', interpreter):
        raise ValueError('Unexpected Nix interpreter')
    trusted(Path(interpreter))
    os.execv(interpreter, [interpreter, '-c', patched, str(runner)])


def core_compose(source: Path = SOURCE, environment: Path = CORE_ENV) -> list[str]:
    return ['docker', 'compose', '--profile', 'speech-proxies', '--env-file', str(environment),
            '-f', str(source / 'deploy/control-plane/compose.yaml')]


def previous_services(rows: list[dict]) -> dict[str, dict]:
    previous = {}
    for row in rows:
        name = row['Name'].removeprefix('/homecompute-control-plane-').removesuffix('-1')
        if name not in CORE_SERVICES or name in previous:
            raise ValueError('Unexpected or duplicated speech adapter')
        labels = row['Config']['Labels']
        compose = labels['com.docker.compose.project.config_files']
        environment = labels['com.docker.compose.project.environment_file']
        if not re.fullmatch(r'/srv/homecompute/releases/[0-9a-f]{40}/deploy/control-plane/compose.yaml', compose):
            raise ValueError('Expected each adapter to have an immutable previous release')
        if environment != str(CORE_ENV) or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['Image']):
            raise ValueError('Unexpected previous speech configuration or image')
        previous[name] = {'compose': compose, 'image_id': row['Image']}
    if set(previous) != set(CORE_SERVICES):
        raise ValueError('Missing previous adapter')
    return previous


def core_stage(revision: str) -> None:
    # Refuse unknown competing overrides; retain the known admission override.
    if DROPIN.exists() and 'HomeCompute speech boundary' not in DROPIN.read_text():
        raise ValueError('Unmanaged speech tunnel override exists')
    _, original = base_runner()
    tunnel_patch(original)
    receipt = STATE / 'before.json'
    if not receipt.exists():
        rows = json.loads(run(['docker', 'inspect', *['homecompute-control-plane-'+x+'-1' for x in CORE_SERVICES]], output=True))
        previous = previous_services(rows)
        # /etc is regenerated by Nix. Retain its exact pre-migration values for
        # rollback without writing the configuration or its secrets to Git.
        environment = CORE_ENV.resolve(strict=True)
        trusted(environment)
        saved_environment = STATE / 'environment.before'
        write(saved_environment, environment.read_text())
        write(receipt, json.dumps({'previous': previous, 'environment': str(saved_environment),
                                  'revision': revision}))
    run(core_compose() + ['config', '--quiet'])
    run(core_compose() + ['build', *CORE_SERVICES])
    # Staging must not change what an unrelated restart will execute. Keep
    # both generated files private until core-apply owns the drained cutover.
    write(STATE / 'runner.pending.py', Path(__file__).read_text(), 0o600)
    write(STATE / 'tunnel.pending.conf', f'''# HomeCompute speech boundary; resolve current Nix base on every start.
[Service]
ExecStart=
ExecStart={BIN}/python3 {INSTALL}/setup-speech-boundary.py run-tunnel
''', 0o600)
    print('Core adapters built; raw-HTTP forwarding files staged privately, inactive.')


def verify_core() -> None:
    observed = []
    for service in CORE_SERVICES:
        name = 'homecompute-control-plane-'+service+'-1'
        run(['docker', 'exec', name, 'python3', '/opt/homecompute/speech-adapter-health.py',
             'tts' if service.startswith('plapre') else 'stt'])
        fields = run(['docker', 'inspect', name, '--format',
            '{{.Image}}\t{{.Config.Image}}\t{{index .Config.Labels "com.docker.compose.project.config_files"}}'], output=True).strip().split('\t')
        if len(fields) != 3 or fields[2] != str(SOURCE / 'deploy/control-plane/compose.yaml'):
            raise ValueError('Running adapter is not from the selected release')
        expected = run(['docker', 'image', 'inspect', fields[1], '--format', '{{.Id}}'], output=True).strip()
        if fields[0] != expected:
            raise ValueError('Adapter image differs from the selected build')
        observed.append({'service': service, 'image_id': fields[0]})
    write(STATE / 'verified.json', json.dumps({'verified_at': time.time(),
        'source_revision': SOURCE.name, 'services': observed}))
    print('Both local Wyoming adapters, selected images and inference dependencies are ready.')


def core_smoke() -> None:
    verify_core()
    stt = r'''import json,socket
from importlib.machinery import SourceFileLoader
m=SourceFileLoader('wyoming_stt','/opt/homecompute/wyoming-openai-stt.py').load_module()
with socket.create_connection(('127.0.0.1',10301),timeout=120) as c:
    f=c.makefile('rb')
    c.sendall(m.event_bytes('transcribe',{'name':'stt-danish','language':'da'}))
    fmt={'rate':16000,'width':2,'channels':1}
    c.sendall(m.event_bytes('audio-start',fmt))
    pcm=b'\0\0'*8000
    data=json.dumps(fmt).encode()
    c.sendall(json.dumps({'type':'audio-chunk','data_length':len(data),'payload_length':len(pcm)}).encode()+b'\n'+data+pcm)
    c.sendall(m.event_bytes('audio-stop'))
    h=json.loads(f.readline(65537)); d=json.loads(f.read(h.get('data_length',0)))
    assert h['type']=='transcript' and isinstance(d.get('text'),str)
'''
    tts = '''import asyncio
from wyoming.client import AsyncClient
from wyoming.tts import Synthesize,SynthesizeVoice
from wyoming.audio import AudioStart,AudioChunk,AudioStop
async def main():
    total=0; started=False
    async with AsyncClient.from_uri('tcp://127.0.0.1:10201',connect_timeout=5,read_timeout=300) as c:
        await c.write_event(Synthesize(text='Hej fra Home Assistant.',voice=SynthesizeVoice(name='danish-default')).event())
        while True:
            e=await c.read_event()
            assert e is not None and e.type!='error'
            if AudioStart.is_type(e.type):
                a=AudioStart.from_event(e); assert (a.rate,a.width,a.channels)==(24000,2,1); started=True
            if AudioChunk.is_type(e.type): total+=len(AudioChunk.from_event(e).audio)
            if AudioStop.is_type(e.type): break
    assert started and 0<total<=48000000 and total%2==0
asyncio.run(main())
'''
    for service, code in [('hviske-wyoming-proxy', stt), ('plapre-wyoming-proxy', tts)]:
        run(['docker', 'exec', 'homecompute-control-plane-'+service+'-1', 'python3', '-c', code])
    write(STATE / 'smoke.json', json.dumps({'verified_at': time.time(), 'synthetic_stt': True,
                                          'synthetic_tts': True, 'pcm': '24000Hz/16bit/mono'}))
    print('Synthetic STT and TTS protocol smoke passed; no transcript/audio retained.')


def core_apply(idle_confirmed: bool) -> None:
    if not idle_confirmed:
        raise ValueError('Drain active text/voice requests, then pass --tunnel-idle-confirmed')
    pending = STATE / 'tunnel.pending.conf'
    runner = STATE / 'runner.pending.py'
    if not pending.exists() or not runner.exists() or not (STATE / 'before.json').exists():
        raise ValueError('Run core-stage first')
    if runner.read_bytes() != Path(__file__).read_bytes():
        raise ValueError('Staged runner differs from the selected release; stage again')
    INSTALL.mkdir(parents=True, exist_ok=True)
    trusted(INSTALL, directory=True)
    write(INSTALL / 'setup-speech-boundary.py', runner.read_text(), 0o755)
    write(DROPIN, pending.read_text(), 0o644)
    try:
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'restart', 'homecompute-compute-ssh-tunnel.service'])
        run(core_compose() + ['up', '-d', '--no-deps', '--no-build', '--wait', '--wait-timeout', '120', *CORE_SERVICES])
        verify_core()
    except Exception:
        core_rollback(idle_confirmed)
        raise


def core_rollback(idle_confirmed: bool) -> None:
    if not idle_confirmed:
        raise ValueError('Rollback requires a drained inference tunnel')
    receipt = json.loads((STATE / 'before.json').read_text())
    previous = receipt['previous']
    if set(previous) != set(CORE_SERVICES) or receipt['environment'] != str(STATE / 'environment.before'):
        raise ValueError('Invalid rollback receipt')
    for name, row in previous.items():
        if not re.fullmatch(r'/srv/homecompute/releases/[0-9a-f]{40}/deploy/control-plane/compose.yaml', row['compose']):
            raise ValueError('Invalid rollback release')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', row['image_id']):
            raise ValueError('Invalid rollback image')
        # Pin the existing image ID even if its old local tag has since moved.
        run(['docker', 'image', 'inspect', row['image_id']])
    _, base = base_runner()
    if ':18201:127.0.0.1:8004' in base or ':18301:127.0.0.1:8003' in base:
        write(DROPIN, f'''# HomeCompute speech boundary explicit legacy rollback.
[Service]
ExecStart=
ExecStart={BIN}/python3 {INSTALL}/setup-speech-boundary.py run-legacy-tunnel
''', 0o644)
    else:
        DROPIN.unlink(missing_ok=True)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'restart', 'homecompute-compute-ssh-tunnel.service'])
    for name, row in previous.items():
        override = STATE / f'rollback-{name}.json'
        write(override, json.dumps({'services': {name: {'image': row['image_id']}}}))
        run(['docker', 'compose', '--profile', 'speech-proxies', '--env-file', receipt['environment'],
             '-f', row['compose'], '-f', str(override), 'up', '-d', '--no-deps', '--no-build',
             '--wait', '--wait-timeout', '120', name])
    print('Each previous adapter image/config and prior tunnel owner restored; GPU models untouched.')


def spark_prepare() -> None:
    runtime = Path('/srv/gb10-ai/runtime/tcp-edge-proxy.py')
    write(runtime, (SOURCE / 'scripts/tcp-edge-proxy.py').read_text(), 0o444)
    account = pwd.getpwnam('homecompute-tunnel')
    keys = Path(account.pw_dir) / '.ssh/authorized_keys'
    if keys.is_symlink() or keys.stat().st_uid not in {0, account.pw_uid} or keys.stat().st_mode & 0o022:
        raise ValueError('Unsafe forwarding authorization')
    original = keys.read_text()
    rows = original.splitlines()
    matches = [i for i, row in enumerate(rows) if row.startswith('from="192.168.30.122",restrict,port-forwarding,') and ' ssh-ed25519 ' in row]
    if len(matches) != 1:
        raise ValueError('Expected exactly one restricted home-core forwarding identity')
    if not (STATE / 'authorized_keys.before').exists():
        write(STATE / 'authorized_keys.before', original)
    for port in (8003, 8004):
        option = f'permitopen="127.0.0.1:{port}"'
        if option not in rows[matches[0]]:
            rows[matches[0]] = rows[matches[0]].replace(' ssh-ed25519 ', ','+option+' ssh-ed25519 ', 1)
    updated = '\n'.join(rows)+'\n'
    if updated != original:
        # Preserve the forwarding account's ownership and never widen hosts.
        temporary = keys.with_name('authorized_keys.speech-tmp')
        with temporary.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), keys.stat().st_uid, keys.stat().st_gid)
            stream.write(updated)
        temporary.replace(keys)
    run(['docker', 'compose', '--env-file', '/etc/gb10-ai/plapre-tts.env', '-f',
         str(SOURCE / 'deploy/compute-node/plapre/compose.yaml'), 'up', '-d', '--no-deps',
         '--wait', '--wait-timeout', '60', 'plapre-http'])
    print('Private Plapre HTTP ingress ready; no GPU model was restarted.')


def spark_legacy(start: bool, core_verified: bool) -> None:
    if not start and not core_verified:
        raise ValueError('Verify both core protocols with synthetic audio before --core-verified')
    for name in SPARK_LEGACY:
        run(['docker', 'update', '--restart=unless-stopped' if start else '--restart=no', name])
        run(['docker', 'start' if start else 'stop', name])
    print('Legacy Spark Wyoming adapters '+('restored.' if start else 'stopped; inference engines unchanged.'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['spark-prepare', 'core-stage', 'core-apply', 'verify',
        'spark-retire', 'spark-rollback', 'core-rollback', 'core-smoke', 'run-tunnel', 'run-legacy-tunnel'])
    parser.add_argument('--revision')
    parser.add_argument('--tunnel-idle-confirmed', action='store_true')
    parser.add_argument('--core-verified', action='store_true')
    args = parser.parse_args()
    expected = 'home-spark' if args.command.startswith('spark-') else 'home-core'
    if os.geteuid() != 0 or socket.gethostname() != expected:
        raise ValueError(f'Run as root on {expected}')
    if args.command in {'run-tunnel', 'run-legacy-tunnel'}:
        run_tunnel(args.command == 'run-legacy-tunnel')
        return
    release_guard(args.revision or '')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    trusted(STATE, directory=True)
    if args.command == 'spark-prepare': spark_prepare()
    elif args.command == 'core-stage': core_stage(args.revision)
    elif args.command == 'core-apply': core_apply(args.tunnel_idle_confirmed)
    elif args.command == 'verify': verify_core()
    elif args.command == 'core-smoke': core_smoke()
    elif args.command == 'core-rollback': core_rollback(args.tunnel_idle_confirmed)
    else: spark_legacy(args.command == 'spark-rollback', args.core_verified)


if __name__ == '__main__':
    main()
