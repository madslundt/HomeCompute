#!/usr/bin/env python3
"""Manage the private Tailscale Serve route for the running T3 Code service."""

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ACCESS = json.loads((ROOT / "deploy/t3code/access.json").read_text())
URL = urlsplit(ACCESS["url"])
BACKEND = ACCESS["backend"]

spec = importlib.util.spec_from_file_location("paperclip_deploy", ROOT / "scripts/deploy-paperclip.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def route_state(config, endpoint, backend):
    port = str(endpoint.port)

    def on_port(host):
        return host.rpartition(":")[2] == port

    if (any(on_port(host) and enabled for host, enabled in (config.get("AllowFunnel") or {}).items())
            or any(port in (session.get("TCP") or {}) for session in (config.get("Foreground") or {}).values())):
        raise ValueError("T3 Code's HTTPS port must be reserved for private background Serve")
    tcp = (config.get("TCP") or {}).get(port)
    web = {host: handler for host, handler in (config.get("Web") or {}).items() if on_port(host)}
    if tcp is None and not web:
        return "absent"
    if tcp == {"HTTPS": True} and web == {endpoint.netloc: {"Handlers": {"/": {"Proxy": backend}}}}:
        return "active"
    raise ValueError("The T3 Code port has an unexpected route")


def status():
    return route_state(json.loads(deploy.run("tailscale", "serve", "status", "--json", capture=True)), URL, BACKEND)


def verify_backend():
    curl = ("curl", "--silent", "--show-error", "--max-time", "10")
    metadata = deploy.run(*curl, "--fail", BACKEND + "/api/auth/session", capture=True)
    session = json.loads(metadata)
    if session.get("authenticated") is not False or session.get("auth", {}).get("policy") != "remote-reachable":
        raise ValueError("T3 Code must require remote pairing before publishing it")
    code = deploy.run(*curl, "--output", "/dev/null", "--write-out", "%{http_code}",
                      BACKEND + "/api/projects", capture=True)
    if code != "401":
        raise ValueError("T3 Code unexpectedly permits unauthenticated project access")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    deploy.published_release()
    if (URL.scheme != "https" or not re.fullmatch(r"home-core\.[a-z0-9-]+\.ts\.net", URL.hostname or "")
            or URL.port in (None, 443) or URL.netloc != f"{URL.hostname}:{URL.port}"
            or URL.path != "/" or URL.query or URL.fragment or BACKEND != "http://127.0.0.1:3773"):
        raise ValueError("Unexpected T3 Code access declaration")
    with open("/var/lib/homecompute/deploy.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        dns = json.loads(deploy.run("tailscale", "status", "--json", capture=True))["Self"]["DNSName"]
        if dns.rstrip(".") != URL.hostname:
            raise ValueError("Tailnet DNS name differs from the published T3 Code address")
        current = status()
        port = f"--https={URL.port}"
        if args.remove:
            if current == "active":
                deploy.run("tailscale", "serve", "--yes", port, "off")
            expected = "absent"
        else:
            verify_backend()
            if current == "absent":
                deploy.run("tailscale", "serve", "--bg", "--yes", port, BACKEND)
            expected = "active"
        if status() != expected:
            raise ValueError("T3 Code Serve configuration did not reach the expected state")
        print(json.dumps({"url": ACCESS["url"], "route": expected}))


if __name__ == "__main__":
    main()
