#!/usr/bin/env python3
"""Apply the published T3 Code virtual host to the running Home Core gateway."""

import fcntl
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/var/lib/homecompute/t3code-gateway")
CADDY = "homecompute-control-plane-caddy-1"
TARGET = "/etc/caddy/Caddyfile"
NETWORK = {
    "name": "homecompute-t3code-ingress",
    "driver": "bridge",
    "internal": True,
    "enable_ipv6": False,
    "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
}

spec = importlib.util.spec_from_file_location("paperclip_deploy", ROOT / "scripts/deploy-paperclip.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def add_route(current, canonical):
    site, = re.findall(r"^t3code\.home\.arpa \{\n.*?^\}\n", canonical, re.M | re.S)
    redirect, = re.findall(r"^\t@t3code host t3code\.home\.arpa\n\thandle @t3code \{\n.*?^\t\}\n",
                           canonical, re.M | re.S)
    if "t3code.home.arpa" in current:
        if current.count(site) != 1 or current.count(redirect) != 1:
            raise ValueError("Existing T3 Code gateway route differs from source")
        return current
    anchor = "http://:8080 {\n"
    if current.count(anchor) != 1:
        raise ValueError("Expected the gateway HTTP redirect listener")
    return current.replace(anchor, anchor + redirect, 1).rstrip() + "\n\n" + site


def current_gateway():
    row, = json.loads(deploy.run("docker", "inspect", CADDY, capture=True))
    labels = row["Config"]["Labels"]
    base, *overlays = labels["com.docker.compose.project.config_files"].split(",")
    if (labels.get("com.docker.compose.project") != "homecompute-control-plane"
            or labels.get("com.docker.compose.service") != "caddy"
            or row["State"]["Health"]["Status"] != "healthy"
            or not re.fullmatch(r"/srv/homecompute/releases/[0-9a-f]{40}/deploy/control-plane/compose.yaml", base)
            or any(not re.fullmatch(r"/var/lib/homecompute/(?:paperclip|t3code-gateway)/[0-9a-f]{40}/caddy.json", p)
                   for p in overlays)):
        raise ValueError("Review the running gateway and its overlays before adding a route")
    compose = ["docker", "compose", "--env-file", "/etc/homecompute/control-plane.env"]
    for path in (base, *overlays):
        compose += ["-f", path]
    expected = "caddy " + labels["com.docker.compose.config-hash"]
    if deploy.run(*compose, "config", "--hash", "caddy", capture=True).strip() != expected:
        raise ValueError("The running gateway differs from its Compose configuration")
    mount, = [item for item in row["Mounts"] if item["Destination"] == TARGET]
    source = Path(mount["Source"])
    if (mount["Type"] != "bind" or mount["RW"] or source.resolve() != source
            or not source.is_file() or source.stat().st_uid != 0 or source.stat().st_mode & 0o022):
        raise ValueError("Expected the gateway to mount a root-protected Caddyfile")
    current = source.read_text()
    if deploy.run("docker", "exec", CADDY, "cat", TARGET, capture=True) != current:
        raise ValueError("The running gateway Caddyfile differs from the mounted source")
    return compose, row, current


def apply(compose, current, candidate):
    deploy.run("docker", "exec", "-i", CADDY, "/bin/sh", "/opt/homecompute/caddy-entrypoint.sh",
               "caddy", "validate", "--config", "-", "--adapter", "caddyfile", data=candidate, capture=True)
    directory = STATE / ROOT.name
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    source, overlay = directory / "Caddyfile", directory / "caddy.json"
    deploy.install(source, candidate, mode=0o644)
    deploy.install(overlay, json.dumps({
        "services": {"caddy": {"volumes": [{"type": "bind", "source": str(source),
                                           "target": TARGET, "read_only": True}], "networks": {"t3code": {}}}},
        "networks": {"t3code": NETWORK},
    }))
    updated = [*compose, "-f", str(overlay)]
    deploy.run(*updated, "config", "--quiet")
    deploy.install(directory / "receipt.json", json.dumps({
        "revision": ROOT.name, "previous_files": [compose[i + 1] for i, x in enumerate(compose) if x == "-f"],
        "before": deploy.digest(current), "after": deploy.digest(candidate),
    }))
    try:
        deploy.replace(updated, "caddy")
        if deploy.run("docker", "exec", CADDY, "cat", TARGET, capture=True) != candidate:
            raise ValueError("The new gateway did not load the selected Caddyfile")
    except (Exception, KeyboardInterrupt):
        deploy.replace(compose, "caddy")
        raise


def main():
    deploy.published_release()
    with open("/var/lib/homecompute/deploy.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compose, row, current = current_gateway()
        candidate = add_route(current, (ROOT / "deploy/control-plane/Caddyfile").read_text())
        if current != candidate:
            apply(compose, current, candidate)
        elif NETWORK["name"] not in row["NetworkSettings"]["Networks"]:
            raise ValueError("The gateway route exists without its dedicated Docker network")
        print(json.dumps({"gateway": "t3code.home.arpa", "caddyfile": deploy.digest(candidate)}))


if __name__ == "__main__":
    main()
