#!/usr/bin/env python3
"""Apply or roll back scoped LiteLLM model changes on Home Core."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY = "https://github.com/madslundt/HomeCompute.git"
RELEASES = Path("/srv/homecompute/releases")
STATE = Path("/var/lib/homecompute")
CONFIG = Path("deploy/control-plane/litellm-config.yaml")
COMPOSE = Path("deploy/control-plane/compose.yaml")
MOUNT = "/etc/litellm/config.yaml"
CONTAINER = "homecompute-control-plane-litellm-1"
SHA = re.compile(r"[0-9a-f]{40}")
MODEL_LIST = re.compile(r"^model_list:\n.*?(?=^litellm_settings:\n)", re.M | re.S)


def subscription_entry(model_list, family, *, optional=False):
    alias = f"chatgpt-{family}-latest"
    entries = list(re.finditer(
        rf'^  - model_name: "{alias}"\n.*?(?=^  - model_name:|\Z)',
        model_list, re.M | re.S,
    ))
    if not entries and optional:
        return None
    expected = (
        f'  - model_name: "{alias}"\n'
        '    litellm_params:\n'
        rf'      model: "chatgpt/gpt-[0-9]+(?:\.[0-9]+)*-{family}"' '\n'
        '    model_info:\n'
        '      mode: responses\n'
        '      input_cost_per_token: 0\n'
        '      output_cost_per_token: 0\n'
    )
    if len(entries) != 1 or not re.fullmatch(expected, entries[0][0]):
        raise ValueError(f"Expected a single generated subscription route for {alias}")
    return entries[0]


def render_aliases(baseline, source):
    base_blocks = list(MODEL_LIST.finditer(baseline))
    source_blocks = list(MODEL_LIST.finditer(source))
    if len(base_blocks) != 1 or len(source_blocks) != 1:
        raise ValueError("Expected a single generated model_list in each config")
    base_block, source_block = base_blocks[0], source_blocks[0]
    model_list = base_block[0]
    sol = subscription_entry(model_list, "sol")
    astra = subscription_entry(model_list, "astra", optional=True)
    desired_sol = subscription_entry(source_block[0], "sol")[0]
    desired_astra = subscription_entry(source_block[0], "astra")[0]

    if astra:
        model_list = model_list[:astra.start()] + desired_astra + model_list[astra.end():]
        sol = subscription_entry(model_list, "sol")
    else:
        desired_sol += desired_astra
    model_list = model_list[:sol.start()] + desired_sol + model_list[sol.end():]
    return baseline[:base_block.start()] + model_list + baseline[base_block.end():]


def render_automation_reasoning(baseline, source):
    entry = re.compile(r'^  - model_name: "automation-moe"\n.*?(?=^  - model_name:|\Z)', re.M | re.S)
    flag = "      supports_reasoning: true\n"
    model_info = "    model_info:\n"
    entries = []
    for config in (baseline, source):
        blocks = list(MODEL_LIST.finditer(config))
        if len(blocks) != 1:
            raise ValueError("Expected a single generated model_list in each config")
        matches = list(entry.finditer(config, blocks[0].start(), blocks[0].end()))
        if len(matches) != 1 or '      model: "openai/automation-qualification"\n' not in matches[0][0]:
            raise ValueError("Expected one automation-moe entry for the Flash-Next deployment")
        entries.append(matches[0])
    current, desired = entries
    if desired[0].count(model_info + flag) != 1:
        raise ValueError("Published automation-moe entry must declare reasoning support")
    if current[0] == desired[0]:
        return baseline
    if current[0] != desired[0].replace(model_info + flag, model_info, 1):
        raise ValueError("Expected reasoning support to be the only automation-moe change")
    return baseline[:current.start()] + desired[0] + baseline[current.end():]


def run(*args, env=None, timeout=30):
    result = subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode:
        operation = args[1] if args[0] != "git" or args[1] != "-C" else args[3]
        raise RuntimeError(f"{args[0]} {operation} failed with exit {result.returncode}")
    return result.stdout


def published_config(revision):
    with tempfile.TemporaryDirectory(prefix="homecompute-litellm-source-") as source:
        run("git", "-C", source, "init", "--quiet")
        run("git", "-C", source, "fetch", "--quiet", "--depth=1",
            REPOSITORY, "refs/heads/master", timeout=180)
        if run("git", "-C", source, "rev-parse", "FETCH_HEAD").strip() != revision:
            raise ValueError("Selected revision must match published origin/master")
        return run("git", "-C", source, "show", f"{revision}:{CONFIG}")


def compose(release, source, *args, timeout=30):
    return run(
        "docker", "compose", "--env-file", "/etc/homecompute/control-plane.env",
        "-f", str(release / COMPOSE), *args,
        env={**os.environ, "LITELLM_CONFIG_FILE": str(source)}, timeout=timeout,
    )


def inspection():
    return json.loads(run("docker", "inspect", CONTAINER))[0]


def mounted_source(container):
    mounts = [mount for mount in container["Mounts"] if mount["Destination"] == MOUNT]
    if len(mounts) != 1 or mounts[0]["Type"] != "bind" or mounts[0]["RW"]:
        raise ValueError("Expected a single read-only LiteLLM config bind mount")
    return Path(mounts[0]["Source"])


def verify_container(release, source, container, *, require_healthy=True):
    labels = container["Config"]["Labels"]
    compose_hash = compose(release, source, "config", "--hash", "litellm").split()
    if (
        labels.get("com.docker.compose.project") != "homecompute-control-plane"
        or labels.get("com.docker.compose.project.config_files") != str(release / COMPOSE)
        or compose_hash != ["litellm", labels.get("com.docker.compose.config-hash")]
        or mounted_source(container) != source
    ):
        raise ValueError("Running LiteLLM Compose configuration differs from the source")
    image = container["Config"]["Image"]
    if not re.search(r"@sha256:[0-9a-f]{64}$", image):
        raise ValueError("LiteLLM image is not digest-pinned")
    if run("docker", "image", "inspect", "--format", "{{.Id}}", image).strip() != container["Image"]:
        raise ValueError("Running LiteLLM image differs from the local digest pin")
    state = container["State"]
    healthy = state["Running"] and state.get("Health", {}).get("Status") == "healthy"
    if require_healthy and not healthy:
        raise ValueError("LiteLLM is not running and healthy")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    if healthy and run("docker", "exec", CONTAINER, "sha256sum", MOUNT).split()[0] != expected:
        raise ValueError("Mounted LiteLLM config differs from its on-disk source")
    return expected


def recreate(release, source, image_id, *, reconcile_legacy_key=True):
    compose(
        release, source, "up", "-d", "--no-deps", "--no-build",
        "--pull", "never", "--wait", "--wait-timeout", "180", "litellm",
        timeout=240,
    )
    container = inspection()
    if container["Image"] != image_id:
        raise ValueError("LiteLLM image changed during the config rollout")
    checksum = verify_container(release, source, container)
    # Older installations can use this scoped lifecycle before the keyless
    # front door is installed. Reconcile only an already provisioned key.
    key_file = Path("/srv/state/control-plane/caddy-data/lan-catalog-key")
    if reconcile_legacy_key and (key_file.exists() or key_file.is_symlink()):
        run(sys.executable, str(Path(__file__).with_name("setup-lan-catalog-key.py")), timeout=180)
    return checksum


def change_config(release, previous, desired, image_id, *, reconcile_legacy_key=True):
    try:
        return recreate(release, desired, image_id, reconcile_legacy_key=reconcile_legacy_key)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        try:
            recreate(release, previous, image_id, reconcile_legacy_key=reconcile_legacy_key)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as rollback:
            raise RuntimeError(f"LiteLLM rollout failed ({error}); rollback failed ({rollback})") from rollback
        raise RuntimeError(f"LiteLLM rollout failed ({error}); prior config restored") from error


def install_config(path, text):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if path.exists():
        if path.read_bytes() != text.encode():
            raise ValueError("Existing generated LiteLLM config requires review")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(text.encode())
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def deploy(action, revision, *, automation_reasoning=False):
    if os.geteuid() != 0 or socket.gethostname() != "home-core":
        raise ValueError("Run as root on home-core")
    with (STATE / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        base_revision = (STATE / "deployed-revision").read_text().strip()
        release = RELEASES / base_revision
        if (
            not SHA.fullmatch(base_revision)
            or Path("/srv/homecompute/current").resolve(strict=True) != release
            or run("git", "-C", str(release), "rev-parse", "HEAD").strip() != base_revision
            or run("git", "-C", str(release), "status", "--porcelain", "--untracked-files=all")
        ):
            raise ValueError("The full-release marker and clean active checkout must agree")

        baseline = release / CONFIG
        directory = "litellm-automation-reasoning" if automation_reasoning else "litellm-chatgpt"
        generated = STATE / directory / f"{base_revision}-{revision}.yaml"
        container = inspection()
        current = mounted_source(container)
        if current not in (baseline, generated):
            raise ValueError("LiteLLM already has a different config mount; review it before applying")
        checksum = verify_container(
            release, current, container, require_healthy=action == "apply" or current == baseline,
        )
        desired = baseline
        if action == "apply":
            base_text = baseline.read_text()
            render = render_automation_reasoning if automation_reasoning else render_aliases
            desired_text = render(base_text, published_config(revision))
            if desired_text != base_text:
                install_config(generated, desired_text)
                desired = generated
        if desired != current:
            checksum = change_config(
                release, current, desired, container["Image"],
                reconcile_legacy_key=not automation_reasoning,
            )
        print(f"LiteLLM {action} verified on host release {base_revision}")
        print(f"Source revision {revision}; mounted config SHA-256 {checksum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "rollback"))
    parser.add_argument("revision")
    parser.add_argument("--automation-reasoning", action="store_true",
                        help="Apply only the automation-moe reasoning capability flag")
    args = parser.parse_args()
    if not SHA.fullmatch(args.revision):
        parser.error("revision must be a full lowercase Git SHA")
    try:
        deploy(args.action, args.revision, automation_reasoning=args.automation_reasoning)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"LiteLLM deployment failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
