#!/usr/bin/env python3
"""Install private ComfyUI SSH forwards and Tailscale Serve on home-core."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import socket
import subprocess

ROOT = Path('/opt/homecompute/comfyui-access')
UNITS = Path('/etc/systemd/system.attached')
BIN = '/run/current-system/sw/bin'
TEXT_UNIT = 'homecompute-compute-ssh-tunnel.service'
TEXT_DROPIN = UNITS / f'{TEXT_UNIT}.d/30-homecompute-llm-admission.conf'
TEXT_OVERRIDE = f'''# HomeCompute temporary LLM admission bridge; base Nix runner is read on every start.
[Service]
ExecStart=
ExecStart={BIN}/python3 {ROOT}/setup-comfyui-access.py --run-text-tunnel
'''
FIREWALL = '''#!/usr/bin/env bash
set -Eeuo pipefail
iptables -w -I INPUT 1 -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193,18194 -j REJECT
while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193,18194 -j HC-COMFYUI 2>/dev/null; do
  iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193,18194 -j HC-COMFYUI
done
while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193 -j HC-COMFYUI 2>/dev/null; do
  iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193 -j HC-COMFYUI
done
while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192 -j HC-COMFYUI 2>/dev/null; do
  iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192 -j HC-COMFYUI
done
while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191 -j HC-COMFYUI 2>/dev/null; do
  iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191 -j HC-COMFYUI
done
while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189 -j HC-COMFYUI 2>/dev/null; do
  iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189 -j HC-COMFYUI
done
while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190 -j HC-COMFYUI 2>/dev/null; do
  iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190 -j HC-COMFYUI
done
iptables -w -N HC-COMFYUI 2>/dev/null || true
iptables -w -F HC-COMFYUI
iptables -w -A HC-COMFYUI -s 172.28.200.2/32 -j ACCEPT
iptables -w -A HC-COMFYUI -j REJECT
iptables -w -I INPUT 1 -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193,18194 -j HC-COMFYUI
for ports in 18188,18189,18190,18191,18192,18193,18194 18188,18189,18190,18191,18192,18193 18188,18189,18190,18191,18192 18188,18189,18190,18191; do
  while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports "$ports" -j REJECT 2>/dev/null; do
    iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports "$ports" -j REJECT
  done
done
'''


def units() -> dict[str, str]:
    return {
        'homecompute-comfyui-firewall': f'''[Unit]
Description=Private ComfyUI tunnel admission for Caddy only
After=docker.service firewall.service
Before=homecompute-comfyui-tunnel.service
PartOf=firewall.service
[Service]
Type=oneshot
RemainAfterExit=yes
Environment=PATH={BIN}
ExecStart={BIN}/bash {ROOT}/firewall.sh
[Install]
WantedBy=multi-user.target
''',
        'homecompute-comfyui-tunnel': f'''[Unit]
Description=Private ComfyUI UI and lifecycle SSH forwards
After=network-online.target docker.service homecompute-comfyui-firewall.service
Requires=homecompute-comfyui-firewall.service
Wants=network-online.target docker.service
StartLimitIntervalSec=0
[Service]
ExecStart={BIN}/ssh -F /dev/null -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -o IdentityAgent=none -o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/etc/homecompute/compute-tunnel/known_hosts -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -i /etc/homecompute/compute-tunnel/id_ed25519 -L 172.28.200.1:18188:127.0.0.1:8188 -L 172.28.200.1:18189:127.0.0.1:8189 -L 172.28.200.1:18191:127.0.0.1:18400 homecompute-tunnel@192.168.30.126
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
UMask=0077
[Install]
WantedBy=multi-user.target
''',
        'homecompute-llm-workflow-activity': f'''[Unit]
Description=Read-only protected LLM workflow activity
After=docker.service homecompute-comfyui-firewall.service
Requires=homecompute-comfyui-firewall.service
[Service]
ExecStart={BIN}/python3 {ROOT}/llm_workflow_activity.py --config {ROOT}/llm-workflow-queue.json
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
RestrictAddressFamilies=AF_INET AF_UNIX
UMask=0077
[Install]
WantedBy=multi-user.target
''',
        'homecompute-llm-admission': f'''[Unit]
Description=Read-only advertised text readiness for new request admission
After=docker.service homecompute-comfyui-firewall.service
Requires=homecompute-comfyui-firewall.service
[Service]
ExecStart={BIN}/python3 {ROOT}/llm_workflow_activity.py --config {ROOT}/llm-workflow-queue.json --admission-only
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
UMask=0077
[Install]
WantedBy=multi-user.target
''',
        'homecompute-comfyui-tailscale': f'''[Unit]
Description=Private Tailscale HTTPS access to ComfyUI
After=tailscaled.service docker.service homecompute-comfyui-tunnel.service
Wants=tailscaled.service homecompute-comfyui-tunnel.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={BIN}/tailscale serve --bg --yes --https=18188 http://172.28.200.2:8194
ExecStop={BIN}/tailscale serve --yes --https=18188 off
[Install]
WantedBy=multi-user.target
''',
    }


def write(path: Path, content: str, mode: int = 0o644) -> None:
    if path.is_symlink() or (path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
        raise ValueError(f'Unsafe installed file: {path}')
    temporary = path.with_suffix('.install-tmp')
    with temporary.open('x') as handle:
        os.chmod(temporary, mode)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def admission_runner(source: str) -> tuple[str, bool]:
    """Change only the one reviewed forward; retain every other runner byte."""
    old, new = ':18005:127.0.0.1:18300', ':18005:127.0.0.1:18400'
    counts = source.count(old), source.count(new)
    if counts == (0, 1):
        return source, False
    if counts != (1, 0):
        raise ValueError('Unsupported or ambiguous compute-tunnel text forward')
    return source.replace(old, new, 1), True


def trusted_nix_file(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (not resolved.is_relative_to('/nix/store') or not resolved.is_file() or
            info.st_uid != 0 or info.st_mode & 0o022):
        raise ValueError('Compute-tunnel base must be a root-owned immutable Nix file')
    return resolved


def text_runner() -> tuple[Path, str]:
    result = subprocess.run([f'{BIN}/systemctl', 'show', TEXT_UNIT, '-p', 'FragmentPath', '--value'],
                            check=True, text=True, capture_output=True)
    fragment = trusted_nix_file(Path(result.stdout.strip()))
    starts = re.findall(r'^ExecStart=(.*)$', fragment.read_text(), re.M)
    pattern = r'/nix/store/[a-z0-9]{32}-homecompute-compute-ssh-tunnel/bin/homecompute-compute-ssh-tunnel'
    if len(starts) != 1 or not re.fullmatch(pattern, starts[0]):
        raise ValueError('Expected one fixed Nix compute-tunnel ExecStart')
    runner = trusted_nix_file(Path(starts[0]))
    return runner, runner.read_text()


def stage_text_tunnel() -> str:
    """Stage only; never restart an inference tunnel carrying active requests."""
    _, source = text_runner()
    patched, needed = admission_runner(source)
    if TEXT_DROPIN.is_symlink() or (TEXT_DROPIN.exists() and (
            TEXT_DROPIN.stat().st_uid != 0 or TEXT_DROPIN.stat().st_mode & 0o022 or
            TEXT_DROPIN.read_text() != TEXT_OVERRIDE)):
        raise ValueError('Refusing an unmanaged compute-tunnel admission override')
    if not needed:
        TEXT_DROPIN.unlink(missing_ok=True)
        return 'native-18400; temporary override removed'
    for directory in (ROOT, TEXT_DROPIN.parent):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o022:
            raise ValueError('Unsafe compute-tunnel override directory')
    write(ROOT / 'compute-ssh-tunnel-admission.sh', patched, 0o755)
    write(ROOT / 'setup-comfyui-access.py', Path(__file__).read_text(), 0o755)
    write(TEXT_DROPIN, TEXT_OVERRIDE)
    return 'staged-18400; restart compute tunnel only after active text requests finish'


def run_text_tunnel() -> None:
    # Resolve the current Nix fragment again at every start. A copied runner
    # alone would pin future forwards/options to today's Nix generation.
    runner, source = text_runner()
    patched, needed = admission_runner(source)
    if not needed:
        os.execv(str(runner), [str(runner)])
    interpreter = patched.splitlines()[0].removeprefix('#!')
    if not re.fullmatch(r'/nix/store/[a-z0-9]{32}-bash-[^/]+/bin/bash', interpreter):
        raise ValueError('Unexpected compute-tunnel interpreter')
    trusted_nix_file(Path(interpreter))
    os.execv(interpreter, [interpreter, '-c', patched, str(runner)])


def install(source: Path) -> None:
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise ValueError('Run as root on home-core')
    for directory in (ROOT, UNITS, UNITS / 'multi-user.target.wants'):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o022:
            raise ValueError(f'Unsafe install directory: {directory}')
    write(ROOT / 'firewall.sh', FIREWALL, 0o755)
    write(ROOT / 'llm_workflow_activity.py', (source / 'scripts/llm_workflow_activity.py').read_text())
    write(ROOT / 'native_llm_activity.py', (source / 'scripts/native_llm_activity.py').read_text())
    write(ROOT / 'llm-workflow-queue.json', (source / 'config/llm-workflow-queue.json').read_text())
    names = units()
    for name, content in names.items():
        target = UNITS / f'{name}.service'
        write(target, content)
        link = UNITS / 'multi-user.target.wants' / target.name
        if link.is_symlink() and link.resolve() != target:
            raise ValueError('Unmanaged boot link')
        if not link.exists():
            link.symlink_to(target)
    subprocess.run(['systemd-analyze', 'verify', *(str(UNITS / f'{name}.service') for name in names)], check=True)
    stage_text_tunnel()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    for name in names:
        subprocess.run(['systemctl', 'restart', f'{name}.service'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--stage-text-tunnel', action='store_true')
    mode.add_argument('--run-text-tunnel', action='store_true')
    args = parser.parse_args()
    if args.stage_text_tunnel or args.run_text_tunnel:
        if os.geteuid() != 0 or socket.gethostname() != 'home-core':
            raise ValueError('Run as root on home-core')
        if args.run_text_tunnel:
            run_text_tunnel()
        else:
            print(stage_text_tunnel())
            subprocess.run(['systemctl', 'daemon-reload'], check=True)
    else:
        install(args.source)
