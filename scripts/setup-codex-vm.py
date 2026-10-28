#!/usr/bin/env python3
"""Provision the approved, separate Codex guest without a shared NixOS switch.

Run only as root on home-core. Existing disks are never overwritten. The initial
maintenance policy allows public HTTP(S); --lock-egress closes forwarding after
guest installation. --stop retains disks/evidence and leaves network fail-closed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess

STATE = Path('/srv/state/codex-vm')
CONFIG = Path('/etc/homecompute/codex-vm')
UNIT_DIR = Path('/etc/systemd/system.attached')
POWERDOWN = Path('/usr/local/libexec/homecompute-codex-vm-powerdown.py')
USER = 'homecompute-codex-vm'
BRIDGE = 'br-hc-codex'
TAP = 'tap-hc-codex'
HOST = '10.77.21.1'
GUEST = '10.77.21.2'
IMAGE_HASH = 'YSssDMG8QTpsuMOP1hF5TK8PK0NsUAE9izeU2xKtc1Q='
KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILT+ES2e5sbGFzBMLOWKZMawBm/kyadBthAldjAmK8Uc mads@home-core-admin'


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def write(path: Path, content: str, mode: int = 0o644) -> None:
    path.write_text(content)
    path.chmod(mode)


def firewall_script() -> str:
    # Guards precede existing Docker/Tailscale/NixOS accept paths. Ingress to
    # private host addresses is checked independently from FORWARD restrictions.
    return '''#!/run/current-system/sw/bin/bash
set -euo pipefail
export PATH=/run/current-system/sw/bin
iptables -w -I INPUT 1 -i br-hc-codex -j REJECT
iptables -w -I FORWARD 1 -i br-hc-codex -j REJECT
iptables -w -I FORWARD 1 -o br-hc-codex -j REJECT
for chain in HC-CODEX-INPUT HC-CODEX-EGRESS HC-CODEX-RETURN; do
  iptables -w -N "$chain" 2>/dev/null || true
  iptables -w -F "$chain"
done
iptables -w -A HC-CODEX-INPUT ! -s 10.77.21.2/32 -j REJECT
iptables -w -A HC-CODEX-INPUT -p tcp --sport 22 -m conntrack --ctstate ESTABLISHED -j ACCEPT
iptables -w -A HC-CODEX-INPUT -d 10.77.21.1/32 -p tcp -m multiport --dports 19444,3129 -j ACCEPT
if [ -f /etc/homecompute/codex-vm/maintenance-egress ]; then
  iptables -w -A HC-CODEX-INPUT -d 10.77.21.1/32 -p tcp --dport 53 -j ACCEPT
  iptables -w -A HC-CODEX-INPUT -d 10.77.21.1/32 -p udp --dport 53 -j ACCEPT
fi
iptables -w -A HC-CODEX-INPUT -j REJECT
iptables -w -A HC-CODEX-EGRESS ! -s 10.77.21.2/32 -j REJECT
for subnet in 0.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 192.0.0.0/24 198.18.0.0/15 224.0.0.0/3; do
  iptables -w -A HC-CODEX-EGRESS -d "$subnet" -j REJECT
done
if [ -f /etc/homecompute/codex-vm/maintenance-egress ]; then
  iptables -w -A HC-CODEX-EGRESS -o enp44s0 -p tcp -m multiport --dports 80,443 -j ACCEPT
fi
iptables -w -A HC-CODEX-EGRESS -j REJECT
iptables -w -A HC-CODEX-RETURN -d 10.77.21.2/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -w -A HC-CODEX-RETURN -j REJECT
while iptables -w -C INPUT -i br-hc-codex -j HC-CODEX-INPUT 2>/dev/null; do iptables -w -D INPUT -i br-hc-codex -j HC-CODEX-INPUT; done
while iptables -w -C FORWARD -i br-hc-codex -j HC-CODEX-EGRESS 2>/dev/null; do iptables -w -D FORWARD -i br-hc-codex -j HC-CODEX-EGRESS; done
while iptables -w -C FORWARD -o br-hc-codex -j HC-CODEX-RETURN 2>/dev/null; do iptables -w -D FORWARD -o br-hc-codex -j HC-CODEX-RETURN; done
iptables -w -I INPUT 1 -i br-hc-codex -j HC-CODEX-INPUT
iptables -w -I FORWARD 1 -o br-hc-codex -j HC-CODEX-RETURN
iptables -w -I FORWARD 1 -i br-hc-codex -j HC-CODEX-EGRESS
iptables -w -t nat -C POSTROUTING -s 10.77.21.2/32 -o enp44s0 -j MASQUERADE 2>/dev/null || iptables -w -t nat -A POSTROUTING -s 10.77.21.2/32 -o enp44s0 -j MASQUERADE
for chain in INPUT FORWARD; do
  ip6tables -w -C "$chain" -i br-hc-codex -j REJECT 2>/dev/null || ip6tables -w -I "$chain" 1 -i br-hc-codex -j REJECT
done
ip6tables -w -C FORWARD -o br-hc-codex -j REJECT 2>/dev/null || ip6tables -w -I FORWARD 1 -o br-hc-codex -j REJECT
while iptables -w -C INPUT -i br-hc-codex -j REJECT 2>/dev/null; do iptables -w -D INPUT -i br-hc-codex -j REJECT; done
while iptables -w -C FORWARD -i br-hc-codex -j REJECT 2>/dev/null; do iptables -w -D FORWARD -i br-hc-codex -j REJECT; done
while iptables -w -C FORWARD -o br-hc-codex -j REJECT 2>/dev/null; do iptables -w -D FORWARD -o br-hc-codex -j REJECT; done
'''


def user_data() -> str:
    marker = json.dumps(dict(schema_version=1, instance_id='homecompute-codex-v1',
                             purpose='codex-worker', work_device='/dev/vdb', work_size_gib=12))
    return f'''#cloud-config
hostname: codex-worker
manage_etc_hosts: true
disable_root: true
ssh_pwauth: false
users:
  - name: codex-operator
    gecos: HomeCompute dedicated Codex operator
    groups: [adm, sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - {KEY}
write_files:
  - path: /etc/homecompute-codex-vm.json
    owner: root:root
    permissions: '0444'
    content: |
      {marker}
  - path: /etc/ssh/sshd_config.d/90-homecompute.conf
    owner: root:root
    permissions: '0644'
    content: |
      PasswordAuthentication no
      PermitRootLogin no
      AllowUsers codex-operator
  - path: /etc/sysctl.d/90-homecompute-codex.conf
    owner: root:root
    permissions: '0644'
    content: |
      net.ipv6.conf.all.disable_ipv6=1
      net.ipv6.conf.default.disable_ipv6=1
runcmd:
  - [sysctl, --system]
  - [systemctl, restart, ssh.service]
  - [sh, -c, 'echo HC_CODEX_SSH_HOST_KEY_BEGIN >/dev/ttyS0; cat /etc/ssh/ssh_host_ed25519_key.pub >/dev/ttyS0; echo HC_CODEX_SSH_HOST_KEY_END >/dev/ttyS0']
'''


def unit(body: str) -> str:
    return body + '\n[Install]\nWantedBy=multi-user.target\n'


def units(qemu: str, dns: str) -> dict[str, str]:
    return {
        'homecompute-codex-network': unit(f'''[Unit]
Description=Dedicated Codex guest bridge
Before=homecompute-codex-firewall.service homecompute-codex-vm.service homecompute-codex-dns.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={CONFIG}/network.sh
'''),
        'homecompute-codex-firewall': unit(f'''[Unit]
Description=Dedicated Codex default deny policy
Requires=homecompute-codex-network.service
After=firewall.service homecompute-codex-network.service
PartOf=firewall.service
Before=homecompute-codex-vm.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={CONFIG}/firewall.sh
ExecReload={CONFIG}/firewall.sh
ExecStop=/run/current-system/sw/bin/iptables -w -I FORWARD 1 -i {BRIDGE} -j REJECT
'''),
        'homecompute-codex-dns': unit(f'''[Unit]
Description=Dedicated Codex public DNS
Requires=homecompute-codex-network.service homecompute-codex-firewall.service
After=homecompute-codex-network.service homecompute-codex-firewall.service
[Service]
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
LoadCredential=Corefile:{CONFIG}/Corefile
ExecStart={dns} -conf %d/Corefile
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
Restart=on-failure
'''),
        'homecompute-codex-vm': unit(f'''[Unit]
Description=Dedicated isolated Codex Ubuntu worker guest
Requires=homecompute-codex-network.service homecompute-codex-firewall.service
After=homecompute-codex-network.service homecompute-codex-firewall.service
[Service]
User={USER}
Group={USER}
SupplementaryGroups=kvm
ExecStart={qemu} -name homecompute-codex -machine q35,accel=kvm -cpu host -smp 2 -m 8192 -nodefaults -display none -serial file:{STATE}/serial.log -no-reboot -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny -qmp unix:{STATE}/qmp.sock,server=on,wait=off -device virtio-rng-pci -drive file={STATE}/root.qcow2,if=none,id=codexroot,format=qcow2,cache=none,discard=unmap -device virtio-blk-pci,drive=codexroot,bootindex=1 -drive file={STATE}/work.qcow2,if=none,id=codexwork,format=qcow2,cache=none,discard=unmap -device virtio-blk-pci,drive=codexwork,serial=hc-codex-work -drive file={STATE}/seed.iso,if=virtio,format=raw,readonly=on -netdev tap,id=codexnet,ifname={TAP},script=no,downscript=no -device virtio-net-pci,netdev=codexnet,mac=52:54:00:77:21:02
ExecStop={POWERDOWN}
TimeoutStopSec=120
KillSignal=SIGTERM
Restart=no
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths={STATE}
DevicePolicy=closed
DeviceAllow=/dev/kvm rw
DeviceAllow=/dev/net/tun rw
MemoryMax=10G
MemorySwapMax=0
CPUQuota=200%
TasksMax=128
LimitCORE=0
UMask=0077
''')}


def provision(xorriso: str) -> None:
    if STATE.exists() or CONFIG.exists():
        raise RuntimeError('Existing Codex VM state/config retained; use explicit maintenance commands')
    candidates = list(Path('/nix/store').glob('*-ubuntu-24.04-server-cloudimg-amd64.img'))
    image = next((p for p in candidates if base64.b64encode(hashlib.file_digest(p.open('rb'), 'sha256').digest()).decode() == IMAGE_HASH), None)
    if not image:
        raise RuntimeError('Exact immutable pinned Ubuntu image missing')
    qemu = next(Path('/nix/store').glob('*-qemu-host-cpu-only-10.2.4/bin/qemu-system-x86_64'))
    dns = next(Path('/nix/store').glob('*-coredns-1.14.6/bin/coredns'))
    if not Path(xorriso).is_file() or not xorriso.startswith('/nix/store/'):
        raise RuntimeError('Pass root-owned Nix-store xorriso binary')
    if not Path('/dev/kvm').is_char_device():
        raise RuntimeError('KVM unavailable')
    mem_available = int(re.search(r'MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text())[1])
    if mem_available < 12 * 1024 * 1024 or shutil.disk_usage('/srv/state').free < 50 * 1024**3:
        raise RuntimeError('Insufficient VM provisioning headroom')
    if run('getent', 'passwd', USER, check=False).returncode != 0:
        run('groupadd', '--system', USER)
        run('useradd', '--system', '--gid', USER, '--groups', 'kvm', '--no-create-home', '--shell', '/run/current-system/sw/bin/nologin', USER)
    owner = pwd.getpwnam(USER)
    STATE.mkdir(mode=0o750)
    os.chown(STATE, owner.pw_uid, owner.pw_gid)
    run('setfacl', '-m', f'u:{USER}:--x', '/srv/state')
    CONFIG.mkdir(parents=True, mode=0o755)
    write(CONFIG / 'user-data', user_data())
    write(CONFIG / 'meta-data', 'instance-id: homecompute-codex-v1\nlocal-hostname: codex-worker\n')
    write(CONFIG / 'network-config', f'''version: 2
ethernets:
  ens3:
    match:
      macaddress: "52:54:00:77:21:02"
    set-name: ens3
    addresses: [{GUEST}/30]
    routes:
      - to: 0.0.0.0/0
        via: {HOST}
    nameservers:
      addresses: [{HOST}]
''')
    run(xorriso, '-as', 'mkisofs', '-quiet', '-volid', 'cidata', '-joliet', '-rock', '-output', str(STATE / 'seed.iso'), str(CONFIG / 'user-data'), str(CONFIG / 'meta-data'), str(CONFIG / 'network-config'))
    for name, size in [('root', 32), ('work', 12)]:
        target = STATE / f'{name}.qcow2'
        if target.exists():
            raise RuntimeError('Refuse disk overwrite')
        if name == 'root':
            run(str(qemu.parent / 'qemu-img'), 'convert', '-f', 'qcow2', '-O', 'qcow2', str(image), str(target))
            run(str(qemu.parent / 'qemu-img'), 'resize', str(target), f'{size}G')
        else:
            run(str(qemu.parent / 'qemu-img'), 'create', '-f', 'qcow2', str(target), f'{size}G')
        target.chmod(0o600)
        os.chown(target, owner.pw_uid, owner.pw_gid)
    os.chown(STATE / 'seed.iso', owner.pw_uid, owner.pw_gid)
    write(CONFIG / 'network.sh', f'''#!/run/current-system/sw/bin/bash
set -euo pipefail
export PATH=/run/current-system/sw/bin
ip link show {BRIDGE} >/dev/null 2>&1 || ip link add {BRIDGE} type bridge
ip addr replace {HOST}/30 dev {BRIDGE}
ip link set {BRIDGE} up
ip link show {TAP} >/dev/null 2>&1 || ip tuntap add dev {TAP} mode tap user {USER} group {USER}
ip link set {TAP} master {BRIDGE}
ip link set {TAP} up
sysctl -w net.ipv4.conf.{BRIDGE}.accept_redirects=0 net.ipv4.conf.{BRIDGE}.send_redirects=0
''', 0o755)
    write(CONFIG / 'firewall.sh', firewall_script(), 0o755)
    write(CONFIG / 'maintenance-egress', 'temporary public HTTP(S) guest bootstrap\n', 0o600)
    write(CONFIG / 'Corefile', f'''.:53 {{
    bind {HOST}
    forward . /etc/resolv.conf
    cache 60
}}
''')
    write(CONFIG / 'powerdown.py', f'''#!/run/current-system/sw/bin/python3
import json, socket, time
s=socket.socket(socket.AF_UNIX)
s.settimeout(5)
s.connect('{STATE}/qmp.sock')
f=s.makefile('rwb')
f.readline()
for command in ['qmp_capabilities', 'system_powerdown']:
    f.write((json.dumps(dict(execute=command))+'\\n').encode()); f.flush(); f.readline()
for _ in range(110):
    try:
        import os
        os.kill(int(os.environ['MAINPID']), 0)
    except ProcessLookupError:
        break
    time.sleep(1)
''', 0o755)
    install_host_units(qemu, dns, image, Path(xorriso))


def install_host_units(qemu: Path, dns: Path, image: Path, xorriso: Path) -> None:
    POWERDOWN.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    write(POWERDOWN, (CONFIG / 'powerdown.py').read_text(), 0o755)
    UNIT_DIR.mkdir(mode=0o755, exist_ok=True)
    unit_files = []
    for name, content in units(str(qemu), str(dns)).items():
        target = UNIT_DIR / (name + '.service')
        if target.exists():
            raise RuntimeError('Refuse existing host unit replacement')
        write(target, content)
        unit_files.append(str(target))
    # Pin every external Nix dependency against GC independently of shared units.
    roots = Path('/nix/var/nix/gcroots/homecompute-codex-vm')
    roots.mkdir(mode=0o755)
    for name, path in [('qemu', qemu.parent.parent), ('dns', dns.parent.parent), ('image', image), ('xorriso', xorriso.parent.parent)]:
        (roots / name).symlink_to(path)
    run('systemd-analyze', 'verify', *unit_files)
    wanted = UNIT_DIR / 'multi-user.target.wants'
    wanted.mkdir(mode=0o755, exist_ok=True)
    for name in units(str(qemu), str(dns)):
        (wanted / (name + '.service')).symlink_to(UNIT_DIR / (name + '.service'))
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', 'homecompute-codex-network', 'homecompute-codex-firewall', 'homecompute-codex-dns', 'homecompute-codex-vm')
    write(CONFIG / 'provision-receipt.json', json.dumps(dict(instance_id='homecompute-codex-v1', image_sha256=IMAGE_HASH, guest=GUEST, host=HOST, work_gib=12, ram_mib=8192, vcpus=2), indent=2)+'\n')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--xorriso')
    actions = p.add_mutually_exclusive_group(required=True)
    actions.add_argument('--apply', action='store_true')
    actions.add_argument('--lock-egress', action='store_true')
    actions.add_argument('--stop', action='store_true')
    actions.add_argument('--finish-staged', action='store_true', help='Finish host units after a partial provisioning failure; never modify disks')
    args = p.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        p.error('Requires root on home-core')
    if args.apply:
        if not args.xorriso:
            p.error('--apply requires --xorriso')
        provision(args.xorriso)
    elif args.finish_staged:
        if not args.xorriso or not Path(args.xorriso).is_file() or not args.xorriso.startswith('/nix/store/'):
            p.error('--finish-staged requires a Nix-store --xorriso binary')
        if (CONFIG / 'meta-data').read_text() != 'instance-id: homecompute-codex-v1\nlocal-hostname: codex-worker\n':
            raise RuntimeError('Staged VM identity mismatch')
        qemu = next(Path('/nix/store').glob('*-qemu-host-cpu-only-10.2.4/bin/qemu-system-x86_64'))
        for name, size in [('root', 32), ('work', 12)]:
            info = json.loads(run(str(qemu.parent / 'qemu-img'), 'info', '--output=json', str(STATE / (name+'.qcow2'))).stdout)
            if info['format'] != 'qcow2' or info['virtual-size'] != size*1024**3:
                raise RuntimeError('Staged disk mismatch')
        image = next(Path('/nix/store').glob('*-ubuntu-24.04-server-cloudimg-amd64.img'))
        if base64.b64encode(hashlib.file_digest(image.open('rb'), 'sha256').digest()).decode() != IMAGE_HASH:
            raise RuntimeError('Pinned image mismatch')
        install_host_units(qemu, next(Path('/nix/store').glob('*-coredns-1.14.6/bin/coredns')), image, Path(args.xorriso))
    elif args.lock_egress:
        (CONFIG / 'maintenance-egress').unlink(missing_ok=True)
        run('systemctl', 'reload', 'homecompute-codex-firewall')
    else:
        (UNIT_DIR / 'multi-user.target.wants/homecompute-codex-vm.service').unlink(missing_ok=True)
        run('systemctl', 'stop', 'homecompute-codex-vm')
        (CONFIG / 'maintenance-egress').unlink(missing_ok=True)
        run('systemctl', 'reload', 'homecompute-codex-firewall')
    action = 'provision' if args.apply else 'finish-staged' if args.finish_staged else 'lock-egress' if args.lock_egress else 'stop'
    print(json.dumps(dict(instance_id='homecompute-codex-v1', action=action)))


if __name__ == '__main__':
    main()
