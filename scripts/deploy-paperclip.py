#!/usr/bin/env python3
"""Deploy Paperclip and add its route and card to the running home-core release."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/var/lib/homecompute/paperclip")
MAINTENANCE = Path("/var/lib/homecompute-docker-maintenance")
HOST = "paperclip.home.arpa"
GATEWAY_CA = "/etc/homecompute/control-plane/home-core-root.crt"


def run(*args, capture=False, data=None, env=None):
    return subprocess.run(args, check=True, text=True, input=data,
                          stdout=subprocess.PIPE if capture else None, env=env).stdout


def render(current, canonical, pattern, anchor):
    matches = re.findall(pattern, canonical, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("Published Paperclip route or card is ambiguous")
    addition = matches[0]
    if addition in current:
        return current
    if HOST in current or (anchor and current.count(anchor) != 1):
        raise ValueError("Existing route or card differs from the Paperclip overlay")
    if not anchor:
        return current.rstrip() + "\n\n" + addition.rstrip() + "\n"
    return current.replace(anchor, anchor + addition, 1)


def digest(data):
    return hashlib.sha256(data.encode()).hexdigest()


def install(path, data, *, mode=0o600):
    if path.exists():
        if path.is_symlink() or path.read_text() != data:
            raise ValueError(f"Existing deployment file differs: {path}")
        return
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.chmod(tmp, mode)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def published_release():
    sha = ROOT.name
    if (os.geteuid() != 0 or socket.gethostname() != "home-core"
            or not re.fullmatch("[0-9a-f]{40}", sha)
            or ROOT.parent != Path("/srv/homecompute/releases")):
        raise ValueError("Run as root from the published release on home-core")
    if run("git", "-C", str(ROOT), "rev-parse", "HEAD", capture=True).strip() != sha:
        raise ValueError("Release SHA differs from its directory")
    if run("git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all", capture=True):
        raise ValueError("Release checkout has local changes")
    run("git", "-C", str(ROOT), "merge-base", "--is-ancestor", sha, "origin/master")


def edge(project, service, destination, canonical, pattern, anchor, bundle,
         *, redirect=False):
    container = f"homecompute-{project}-{service}-1"
    row = json.loads(run("docker", "inspect", container, capture=True))[0]
    labels = row["Config"]["Labels"]
    files = labels["com.docker.compose.project.config_files"].split(",")
    existing = ["docker", "compose", "--env-file", f"/etc/homecompute/{project}.env"]
    for file in files:
        existing.extend(("-f", file))
    if (labels.get("com.docker.compose.project") != f"homecompute-{project}"
            or labels.get("com.docker.compose.service") != service
            or not row["State"]["Running"]
            or len(files) != 1
            or not re.fullmatch(rf"/srv/homecompute/releases/[a-f0-9]{{40}}/deploy/{project}/compose.yaml", files[0])
            or run(*existing, "config", "--hash", service, capture=True).strip()
            != f"{service} {labels['com.docker.compose.config-hash']}"):
        raise ValueError(f"Running {service} differs from its Compose release")
    mount, = [item for item in row["Mounts"] if item["Destination"] == destination]
    if mount["Type"] != "bind" or mount["RW"]:
        raise ValueError(f"Unexpected {service} source mount")
    source = Path(mount["Source"])
    if not redirect:
        source /= "services.yaml"
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Expected regular configuration for {service}")
    before = source.read_text()
    canonical = (ROOT / canonical).read_text()
    candidate = render(before, canonical, pattern, anchor)
    if redirect:
        candidate = render_redirect(candidate, canonical)
    target = bundle / source.name
    install(target, candidate, mode=0o644)
    # The original config directory mount remains in place for Homepage's other files.
    target_path = destination if redirect else destination + "/services.yaml"
    override = {"services": {service: {"volumes": [
        {"type": "bind", "source": str(target), "target": target_path, "read_only": True},
    ]}}}
    if redirect:
        override["services"][service]["networks"] = {"paperclip": {}}
        override["networks"] = {"paperclip": {
            "name": "homecompute-paperclip", "driver": "bridge",
            "internal": True, "enable_ipv6": False,
            "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
        }}
        run("docker", "exec", "-i", container, "/bin/sh", "/opt/homecompute/caddy-entrypoint.sh",
            "caddy", "validate", "--config", "-", "--adapter", "caddyfile", data=candidate, capture=True)
    overlay = bundle / f"{service}.json"
    install(overlay, json.dumps(override, sort_keys=True) + "\n")
    updated = [*existing, "-f", str(overlay)]
    run(*updated, "config", "--quiet")
    return existing, updated, {"image": row["Image"], "baseline": str(source),
                               "before": digest(before), "after": digest(candidate)}


def render_redirect(current, canonical):
    pattern = r"^\t@paperclip host paperclip\.home\.arpa\n\thandle @paperclip \{\n.*?^\t\}\n"
    addition, = re.findall(pattern, canonical, re.MULTILINE | re.DOTALL)
    if addition in current:
        return current
    anchor = "http://:8080 {\n"
    if "@paperclip" in current or current.count(anchor) != 1:
        raise ValueError("Existing Caddy redirect differs from the Paperclip overlay")
    return current.replace(anchor, anchor + addition, 1)


def verify():
    address = json.loads((ROOT / "config/paperclip-dns.json").read_text())["records"][0]["value"]
    curl = ("curl", "--silent", "--show-error", "--max-time", "15")
    # Docker hairpins a host-side request through its bridge IP, which Caddy
    # correctly denies. Verify TLS at the published listener and check the
    # upstream separately from the Caddy container.
    status = run(*curl, "--cacert", GATEWAY_CA, "--resolve", f"{HOST}:443:{address}",
                 "--output", "/dev/null", "--write-out", "%{http_code}",
                 f"https://{HOST}/api/health", capture=True)
    if status not in {"200", "403"}:
        raise ValueError(f"Paperclip HTTPS listener returned HTTP {status}")
    body = run("docker", "exec", "homecompute-control-plane-caddy-1", "wget", "-qO-",
               "-T", "10", "--header", f"Host: {HOST}",
               "http://paperclip:3100/api/health", capture=True)
    health = json.loads(body)
    if health.get("status") != "ok" or health.get("deploymentMode") != "authenticated":
        raise ValueError("Paperclip did not report authenticated readiness")
    card = run(*curl, "--fail", "-H", "Host: home-core",
               "http://127.0.0.1/api/services", capture=True)
    if f"https://{HOST}/" not in card:
        raise ValueError("Homepage API does not return the Paperclip link")


def replace(compose, service):
    run(*compose, "up", "-d", "--no-deps", "--pull", "never", "--wait", "--wait-timeout", "180", service)


def deploy():
    if any(Path("/var/lib/homecompute/homepage-maintenance").glob("active-*.json")):
        raise ValueError("Reconcile Homepage maintenance overrides before deploying")
    if any(MAINTENANCE.glob("*.active")):
        raise ValueError("Reconcile active Docker maintenance before deploying")
    bundle = STATE / ROOT.name
    bundle.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (bundle / "applied").is_file():
        verify()
        print(f"Healthy scoped Paperclip deployment: {ROOT.name}")
        return
    caddy, next_caddy, caddy_report = edge(
        "control-plane", "caddy", "/etc/caddy/Caddyfile", "deploy/control-plane/Caddyfile",
        r"^paperclip\.home\.arpa \{\n.*?^\}\n", "", bundle, redirect=True,
    )
    homepage, next_homepage, homepage_report = edge(
        "homepage", "homepage", "/app/config", "deploy/homepage/config/services.yaml",
        r"^    - Paperclip:\n.*?(?=^    - |^- |\Z)", "- AI assistants:\n", bundle,
    )
    install(bundle / "receipt.json", json.dumps({
        "revision": ROOT.name, "caddy": caddy_report, "homepage": homepage_report,
    }, indent=2, sort_keys=True) + "\n")
    secret = STATE / "environment"
    data = run("sops", "decrypt", "--extract", '["paperclip/environment"]',
               str(ROOT / "secrets/paperclip.sops.yaml"), capture=True,
               env={**os.environ, "SOPS_AGE_KEY_FILE": "/var/lib/sops-nix/key.txt"})
    if not re.fullmatch(r"BETTER_AUTH_SECRET=[a-f0-9]{64}\nPAPERCLIP_TOOL_ACTION_SIGNING_SECRET=[a-f0-9]{64}\n?", data):
        raise ValueError("Paperclip signing secret format is invalid")
    install(secret, data)
    state = Path("/srv/state/paperclip")
    if state.is_symlink():
        raise ValueError("Paperclip requires a real state directory")
    run("install", "-d", "-m", "0700", "-o", "1000", "-g", "1000", str(state))
    os.environ["PAPERCLIP_SECRETS_FILE"] = str(secret)
    os.environ["PAPERCLIP_SECCOMP_PROFILE"] = str(ROOT / "deploy/paperclip/codex-seccomp.json")
    paperclip = ["docker", "compose", "-f", str(ROOT / "deploy/paperclip/compose.yaml")]
    run(*paperclip, "config", "--quiet")
    run(*paperclip, "pull")
    if run(*paperclip, "ps", "-aq", "paperclip", capture=True).strip():
        raise ValueError("Existing Paperclip container requires its current deployment lifecycle")
    rollback = []
    try:
        rollback.append((*caddy, "up", "-d", "--no-deps", "--pull", "never", "caddy"))
        replace(next_caddy, "caddy")
        rollback.append((*paperclip, "down"))
        run(*paperclip, "up", "-d", "--wait", "--wait-timeout", "180")
        rollback.append((*homepage, "up", "-d", "--no-deps", "--pull", "never", "homepage"))
        replace(next_homepage, "homepage")
        verify()
    except (Exception, KeyboardInterrupt):
        for command in reversed(rollback):
            subprocess.run(command, check=False)
        raise
    install(bundle / "applied", ROOT.name + "\n")
    print(f"Healthy scoped Paperclip deployment: {ROOT.name}")


if __name__ == "__main__":
    try:
        os.umask(0o077)
        published_release()
        with ExitStack() as locks:
            for path in (Path("/var/lib/homecompute/deploy.lock"), MAINTENANCE / "operation.lock"):
                fcntl.flock(locks.enter_context(path.open("a")), fcntl.LOCK_EX | fcntl.LOCK_NB)
            deploy()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Paperclip deployment stopped: {error}", file=sys.stderr)
        sys.exit(1)
