"""Run the existing TLS proxy on Orca's private bridge only."""
import json
from pathlib import Path

from openclaw_site_egress import Proxy


if __name__ == "__main__":
    config = Path("/etc/homecompute-orca")
    hosts = set(json.loads((config / "egress-hosts.json").read_bytes()))
    claude = config / "egress-claude-hosts.json"
    if claude.is_file():
        hosts.update(json.loads(claude.read_bytes()))
    with Proxy(("172.28.218.4", 3128), frozenset(hosts), tunnel_seconds=300) as proxy:
        proxy.serve_forever()
