#!/usr/bin/env python3
"""Apply the per-container outbound policy for the OpenBot bridge network."""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import subprocess
import sys

CONFIG = Path('/etc/homecompute-openbot/network.json')
PARTNER_CONFIG = Path('/etc/homecompute-openbot-partner/current/openbot-network.json')
CHAIN = 'HC-OPENBOT-EGRESS'
SUBNET = ipaddress.ip_network('172.30.192.0/24')
CONTAINER = ipaddress.ip_address('172.30.192.2')
PARTNER_CHAIN = 'HC-OB-PARTNER-EGRESS'
PARTNER_BRIDGE = 'hc-ob-partner'
PARTNER_SUBNET = ipaddress.ip_network('172.30.196.0/24')
PARTNER_CONTAINER = ipaddress.ip_address('172.30.196.2')
HOST = ipaddress.ip_address('10.77.20.1')
LOCAL_MODEL_PORT = 18080
ANDROID_MCP_PORT = 19445
MCP_GATEWAY_PORT = 443
BLOCKED = (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
    '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24',
    '192.0.2.0/24', '192.168.0.0/16', '198.18.0.0/15',
    '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4',
)


def load_turn_addresses(partner: bool = False) -> list[ipaddress.IPv4Address]:
    value = json.loads((PARTNER_CONFIG if partner else CONFIG).read_text())
    if value.get('schema_version') != 1:
        raise ValueError('unsupported OpenBot network configuration')
    if value.get('docker_subnet') != str(SUBNET) or value.get('container_address') != str(CONTAINER):
        raise ValueError('OpenBot Docker network does not match the reviewed policy')
    if partner and value.get('partner') != {
        'bridge': PARTNER_BRIDGE,
        'docker_subnet': str(PARTNER_SUBNET),
        'container_address': str(PARTNER_CONTAINER),
    }:
        raise ValueError('Partner OpenBot Docker network does not match the reviewed policy')
    endpoint = value.get('local_model_endpoint', {})
    if endpoint != {'address': str(HOST), 'port': LOCAL_MODEL_PORT}:
        raise ValueError('OpenBot local model endpoint does not match the reviewed boundary')
    android_mcp = value.get('android_mcp_endpoint', {})
    if android_mcp != {'address': str(HOST), 'port': ANDROID_MCP_PORT}:
        raise ValueError('OpenBot Android MCP endpoint does not match the reviewed boundary')
    mcp_gateway = value.get('mcp_gateway_endpoint', {})
    if mcp_gateway != {'address': str(HOST), 'port': MCP_GATEWAY_PORT}:
        raise ValueError('OpenBot LiteLLM MCP gateway endpoint does not match the reviewed boundary')
    turn = value.get('turn_client_ports', {})
    if turn != {'udp_listeners': [3478], 'udp_relay_range': [49152, 65535],
                'tcp_listeners': [3478, 5349]}:
        raise ValueError('OpenBot TURN ports do not match the reviewed policy')
    servers = value.get('turn_servers')
    if not isinstance(servers, list) or not servers:
        raise ValueError('at least one reviewed OpenBot TURN endpoint is required')
    addresses = []
    for server in servers:
        address = ipaddress.ip_address(server.get('address', ''))
        if address.version != 4 or not address.is_global:
            raise ValueError('TURN endpoints must be public IPv4 addresses')
        if not isinstance(server.get('hostname'), str) or not server['hostname'].endswith('.openbot.run'):
            raise ValueError('TURN endpoint hostname is outside openbot.run')
        addresses.append(address)
    return sorted(set(addresses))


def iptables(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['iptables', '-w', *arguments], text=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=check)


def remove_rule(table_chain: str, *rule: str) -> None:
    while iptables('-C', table_chain, *rule, check=False).returncode == 0:
        iptables('-D', table_chain, *rule)


def egress_jump(partner: bool) -> tuple[str, ...]:
    if partner:
        return ('-i', PARTNER_BRIDGE, '-j', PARTNER_CHAIN)
    return ('-s', str(SUBNET), '-j', CHAIN)


def remove(partner: bool = False) -> None:
    chain = PARTNER_CHAIN if partner else CHAIN
    remove_rule('DOCKER-USER', *egress_jump(partner))
    if partner:
        remove_rule('INPUT', '-i', PARTNER_BRIDGE, '-j', 'REJECT')
    if iptables('-L', chain, check=False).returncode == 0:
        iptables('-F', chain)
        iptables('-X', chain)


def apply(partner: bool = False) -> None:
    if iptables('-L', 'DOCKER-USER', check=False).returncode != 0:
        raise RuntimeError('Docker DOCKER-USER chain is unavailable')
    addresses = load_turn_addresses(partner)
    container, chain = (PARTNER_CONTAINER, PARTNER_CHAIN) if partner else (CONTAINER, CHAIN)
    if partner:
        # INPUT covers connections to any guest-local address. The temporary
        # FORWARD guard also blocks traffic while the egress chain is rebuilt.
        guest_deny = ('-i', PARTNER_BRIDGE, '-j', 'REJECT')
        if iptables('-C', 'INPUT', *guest_deny, check=False).returncode != 0:
            iptables('-I', 'INPUT', '1', *guest_deny)
        iptables('-I', 'DOCKER-USER', '1', *guest_deny)
    iptables('-N', chain, check=False)
    iptables('-F', chain)
    iptables('-A', chain, '!', '-s', f'{container}/32', '-j', 'REJECT')
    iptables('-A', chain, '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT')
    iptables('-A', chain, '-d', f'{HOST}/32', '-p', 'udp', '--dport', '53', '-j', 'ACCEPT')
    iptables('-A', chain, '-d', f'{HOST}/32', '-p', 'tcp', '--dport', '53', '-j', 'ACCEPT')
    iptables('-A', chain, '-d', f'{HOST}/32', '-p', 'tcp', '--dport', str(LOCAL_MODEL_PORT), '-j', 'ACCEPT')
    if not partner:
        iptables('-A', chain, '-d', f'{HOST}/32', '-p', 'tcp', '--dport', str(ANDROID_MCP_PORT), '-j', 'ACCEPT')
        iptables('-A', chain, '-d', f'{HOST}/32', '-p', 'tcp', '--dport', str(MCP_GATEWAY_PORT), '-j', 'ACCEPT')
    for address in addresses:
        for port in ('3478', '49152:65535'):
            iptables('-A', chain, '-d', f'{address}/32', '-p', 'udp', '--dport', port, '-j', 'ACCEPT')
        iptables('-A', chain, '-d', f'{address}/32', '-p', 'tcp', '-m', 'multiport',
                 '--dports', '3478,5349', '-j', 'ACCEPT')
    for blocked_subnet in BLOCKED:
        iptables('-A', chain, '-d', blocked_subnet, '-j', 'REJECT')
    iptables('-A', chain, '-p', 'tcp', '-m', 'multiport', '--dports', '80,443', '-j', 'ACCEPT')
    iptables('-A', chain, '-j', 'REJECT')
    jump = egress_jump(partner)
    if iptables('-C', 'DOCKER-USER', *jump, check=False).returncode != 0:
        iptables('-I', 'DOCKER-USER', '1', *jump)
    if partner:
        remove_rule('DOCKER-USER', '-i', PARTNER_BRIDGE, '-j', 'REJECT')


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in {'apply', 'remove'} or args[1:] not in ([], ['partner']):
        print('Usage: network-policy.py apply|remove [partner]', file=sys.stderr)
        return 2
    partner = args[1:] == ['partner']
    if args[0] == 'remove':
        remove(partner)
    else:
        apply(partner)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f'OpenBot network policy failed: {exc}', file=sys.stderr)
        raise SystemExit(1) from None
