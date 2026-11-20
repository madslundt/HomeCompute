#!/usr/bin/env python3
"""Apply the published T3 Code card to the running Home Core Homepage project."""

import argparse
from contextlib import ExitStack
import fcntl
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/var/lib/homecompute/t3code-homepage")
PROJECT = "homecompute-homepage"
TARGET = "/app/config/services.yaml"
GROUP = "- AI assistants:\n"
ACCESS_URL = json.loads((ROOT / "deploy/t3code/access.json").read_text())["url"]
CARD = r"^    - T3 Code:\n.*?(?=^    - |^- |\Z)"

LEGACY_CARD = """    - T3 Code:
        id: homecore-t3code
        icon: mdi-code-braces
        href: http://127.0.0.1:3773/
        description: Coding workspace · requires the Home Core SSH tunnel

"""

spec = importlib.util.spec_from_file_location("paperclip_deploy", ROOT / "scripts/deploy-paperclip.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def add_card(current, canonical):
    matches = re.findall(CARD, canonical, re.M | re.S)
    if (len(matches) != 1 or current.count(GROUP) != 1
            or not canonical.partition(GROUP)[2].startswith(matches[0])):
        raise ValueError("Expected a single canonical T3 Code card under AI assistants")
    existing = re.findall(CARD, current, re.M | re.S)
    if existing or "homecore-t3code" in current:
        assistant_cards = re.split(r"^- ", current.partition(GROUP)[2], maxsplit=1, flags=re.M)[0]
        if existing == [LEGACY_CARD] and current.count("homecore-t3code") == 1 and LEGACY_CARD in assistant_cards:
            return current.replace(LEGACY_CARD, matches[0], 1)
        if (existing != matches or current.count("homecore-t3code") != 1 or matches[0] not in assistant_cards):
            raise ValueError("Existing T3 Code card differs from the published card")
        return current
    return current.replace(GROUP, GROUP + matches[0], 1)


def file_mount(source):
    return {"type": "bind", "source": str(source), "target": TARGET, "read_only": True, "bind": {"create_host_path": False}}


def render(compose):
    return json.loads(deploy.run(*compose, "config", "--format", "json", capture=True))


def current_homepage():
    container = f"{PROJECT}-homepage-1"
    row, = json.loads(deploy.run("docker", "inspect", container, capture=True))
    labels = row["Config"]["Labels"]
    base, *overlays = labels["com.docker.compose.project.config_files"].split(",")
    if (labels.get("com.docker.compose.project") != PROJECT or labels.get("com.docker.compose.service") != "homepage"
            or row["State"]["Health"]["Status"] != "healthy"
            or not re.fullmatch(r"/srv/homecompute/releases/[0-9a-f]{40}/deploy/homepage/compose.yaml", base)
            or any(not re.fullmatch(r"/var/lib/homecompute/(?:paperclip|t3code-homepage)/[0-9a-f]{40}/homepage.json", p)
                   for p in overlays)):
        raise ValueError("Review Homepage's current release and overlays before applying a card")
    compose = ["docker", "compose", "--env-file", "/etc/homecompute/homepage.env", "-f", base]
    for overlay in overlays:
        compose += ["-f", overlay]
    if deploy.run(*compose, "config", "--hash", "homepage", capture=True).strip() != (
            "homepage " + labels["com.docker.compose.config-hash"]):
        raise ValueError("Running Homepage differs from its Compose configuration")
    if list(Path("/var/lib/homecompute/homepage-maintenance").glob("active-*.json")):
        raise ValueError("Reconcile active Homepage maintenance before changing its configuration")
    mounts = {m["Destination"]: m for m in row["Mounts"]}
    entry = mounts.get(TARGET) or mounts["/app/config"]
    if entry["Type"] != "bind" or entry["RW"]:
        raise ValueError("Expected a read-only Homepage configuration bind mount")
    path = Path(entry["Source"])
    if TARGET not in mounts:
        path /= "services.yaml"
    if path.resolve() != path or not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
        raise ValueError("Expected a root-protected Homepage services file")
    before = path.read_text()
    if deploy.run("docker", "exec", container, "cat", TARGET, capture=True) != before:
        raise ValueError("Running Homepage services differ from the mounted source")
    return compose, before


def check_card():
    body = deploy.run("curl", "--fail", "--silent", "--show-error", "--max-time", "10",
                      "-H", "Host: home-core", "http://127.0.0.1/api/services", capture=True)
    groups = json.loads(body)
    links = [service.get("href") for group in groups if group.get("name") == "AI assistants"
             for service in group["services"] if service.get("id") == "homecore-t3code"]
    if links != [ACCESS_URL]:
        raise ValueError("Homepage API did not return the expected T3 Code card")


def apply(compose, before, after):
    bundle = STATE / ROOT.name
    bundle.mkdir(mode=0o700, parents=True, exist_ok=True)
    services, overlay = bundle / "services.yaml", bundle / "homepage.json"
    deploy.install(services, after, mode=0o644)
    deploy.install(overlay, json.dumps({"services": {"homepage": {"volumes": [file_mount(services)]}}}))
    updated = [*compose, "-f", str(overlay)]
    baseline = render(compose)
    expected = render(updated)
    for config in (baseline, expected):
        volumes = config["services"]["homepage"]["volumes"]
        config["services"]["homepage"]["volumes"] = [v for v in volumes if v["target"] != TARGET]
    if baseline != expected:
        raise ValueError("The proposed overlay changes Homepage beyond its services mount")
    deploy.install(bundle / "receipt.json", json.dumps({
        "revision": ROOT.name, "previous_files": [compose[i + 1] for i, x in enumerate(compose) if x == "-f"],
        "before": deploy.digest(before), "after": deploy.digest(after),
    }))
    try:
        deploy.replace(updated, "homepage")
        check_card()
    except (Exception, KeyboardInterrupt):
        deploy.replace(compose, "homepage")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-before-sha256")
    args = parser.parse_args()
    deploy.published_release()
    with ExitStack() as stack:
        for path in ("/var/lib/homecompute/deploy.lock", "/var/lib/homecompute-docker-maintenance/operation.lock"):
            lock = stack.enter_context(open(path, "a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compose, before = current_homepage()
        after = add_card(before, (ROOT / "deploy/homepage/config/services.yaml").read_text())
        before_hash = deploy.digest(before)
        if args.apply:
            if args.expected_before_sha256 != before_hash:
                raise ValueError("Supply the reviewed before hash to apply the card")
            if before == after:
                check_card()
            else:
                apply(compose, before, after)
        print(json.dumps({"revision": ROOT.name, "before": before_hash, "after": deploy.digest(after), "applied": args.apply}))


if __name__ == "__main__":
    main()
