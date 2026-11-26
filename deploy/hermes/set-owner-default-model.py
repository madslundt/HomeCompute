#!/usr/bin/env python3
"""Apply the owner-approved Sol route to the existing standalone Hermes profile."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

import yaml

HOME = Path("/sandbox/.hermes")
ENDPOINT = "http://ai.home.arpa:18080/v1"
MODEL = "chatgpt-sol-latest"
KEY_ENV = "HOMECOMPUTE_CHATGPT_API_KEY"
PROVIDER_NAME = "homecompute-sol"
PROVIDER = f"custom:{PROVIDER_NAME}"
PROVIDER_ENTRY = {
    "name": PROVIDER_NAME,
    "base_url": ENDPOINT,
    "default_model": MODEL,
    "api_mode": "chat_completions",
    "key_env": KEY_ENV,
}
RECEIPT = HOME / ".homecompute-owner-sol-before.json"
CONFIG = HOME / "config.yaml"
ENV = HOME / ".env"


def read(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o027):
        raise ValueError("expected a private, owner-controlled Hermes file")
    return path.read_bytes()


def replace(path, value, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            os.fchmod(tmp.fileno(), mode)
            tmp.write(value)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def digest(config, env):
    return hashlib.sha256(config + b"\x00" + env).hexdigest()


def key_lines(env):
    return [line for line in env.splitlines() if line.startswith(KEY_ENV.encode() + b"=")]


def desired(config, env, sol_config, sol_env):
    current = yaml.safe_load(config)
    model = current["model"]
    sol_model = yaml.safe_load(sol_config)["model"]
    if (model.get("provider") not in {"custom", PROVIDER} or model.get("base_url") != ENDPOINT
            or model.get("default") not in {"automation-moe", MODEL}):
        raise ValueError("owner model has an unexpected preimage")
    if (sol_model.get("provider") != "custom" or sol_model.get("base_url") != ENDPOINT
            or sol_model.get("default") != MODEL or sol_model.get("key_env") != KEY_ENV
            or sol_model.get("api_key")):
        raise ValueError("existing owner Sol credential is not bound to the expected route")
    lines = key_lines(sol_env)
    if len(lines) != 1 or not re.fullmatch(KEY_ENV.encode() + rb"=sk-[A-Za-z0-9_-]+", lines[0]):
        raise ValueError("the scoped Sol credential is missing or malformed")
    if key_lines(env) not in ([], lines):
        raise ValueError("owner credential already exists with a different value")
    providers = current.setdefault("providers", {})
    if not isinstance(providers, dict) or providers.get(PROVIDER_NAME) not in (None, PROVIDER_ENTRY):
        raise ValueError("existing named Sol provider differs")
    if model.get("default") == MODEL:
        if (model.get("provider") != PROVIDER or providers.get(PROVIDER_NAME) != PROVIDER_ENTRY
                or model.get("api_key") or key_lines(env) != lines):
            raise ValueError("existing Sol model binding differs")
        return config, env
    if (model.get("provider") != "custom" or model.get("key_env")
            or model.get("api_key") != "${COMPATIBLE_API_KEY}"):
        raise ValueError("unexpected local credential binding")
    model.pop("api_key")
    providers[PROVIDER_NAME] = PROVIDER_ENTRY
    model.update(default=MODEL, provider=PROVIDER, key_env=KEY_ENV)
    if not key_lines(env):
        env = env.rstrip(b"\n") + b"\n" + lines[0] + b"\n"
    return yaml.safe_dump(current, sort_keys=False).encode(), env


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=["plan", "apply", "rollback"])
    parser.add_argument("--expected")
    args = parser.parse_args()
    if HOME.is_symlink() or Path(os.environ.get("HERMES_HOME", "")) != HOME:
        raise ValueError("run inside the existing standalone owner Hermes")
    before_config, before_env = read(CONFIG), read(ENV)
    current = digest(before_config, before_env)
    config_mode = stat.S_IMODE(CONFIG.stat().st_mode)
    env_mode = stat.S_IMODE(ENV.stat().st_mode)
    if args.operation == "rollback":
        state = json.loads(read(RECEIPT))
        old_config, old_env = bytes.fromhex(state["config"]), bytes.fromhex(state["env"])
        if current not in {state["before"], state["applied"], state["environment_only"]}:
            raise ValueError("owner configuration changed after the model update")
        replace(CONFIG, old_config, config_mode)
        replace(ENV, old_env, env_mode)
        RECEIPT.unlink()
        print("Owner model restored")
        return
    sol_profile = HOME / "profiles/chatgpt-sol"
    if sol_profile.is_symlink() or sol_profile.parent.is_symlink():
        raise ValueError("the scoped Sol profile must be a real directory")
    after_config, after_env = desired(
        before_config, before_env, read(sol_profile / "config.yaml"), read(sol_profile / ".env")
    )
    print(json.dumps({"before": current, "default": MODEL, "already_selected": after_config == before_config}))
    if args.operation == "plan":
        return
    if args.expected != current:
        raise ValueError("apply requires the expected owner configuration digest")
    if (before_config, before_env) == (after_config, after_env):
        return
    state = {
        "config": before_config.hex(),
        "env": before_env.hex(),
        "before": current,
        "applied": digest(after_config, after_env),
        "environment_only": digest(before_config, after_env),
    }
    fd = os.open(RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as receipt:
        json.dump(state, receipt)
        receipt.flush()
        os.fsync(receipt.fileno())
    try:
        replace(ENV, after_env, env_mode)
        replace(CONFIG, after_config, config_mode)
        if (read(CONFIG), read(ENV)) != (after_config, after_env):
            raise ValueError("owner model readback differs")
    except (OSError, ValueError):
        replace(CONFIG, before_config, config_mode)
        replace(ENV, before_env, env_mode)
        RECEIPT.unlink()
        raise


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        raise SystemExit(f"Owner model operation stopped ({type(error).__name__}).") from None
