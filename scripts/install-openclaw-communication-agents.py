#!/usr/bin/env python3
"""Install per-user Mac communication supervision using existing private state."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "dk.homecompute.openclaw-"


def definitions(home: Path, python: str) -> dict[str, dict]:
    config = home / ".config/homecompute/openclaw"
    state = home / ".local/state/homecompute/openclaw-communication"
    ssh = ["/usr/bin/ssh", "-N", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20",
           "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=10"]
    commands = {
        "adapter": [python, str(ROOT / "scripts/openclaw-communication.py"),
                    "--config", str(config / "communication.json"),
                    "--tokens", str(config / "transport-tokens.json"),
                    "--state", str(state / "adapter"),
                    "--task-transport", str(config / "codex-task-transport.json")],
        "tunnel": ssh + ["-R", "127.0.0.1:18793:127.0.0.1:18793", "home-core"],
        "task-tunnel": ssh + ["-L", "127.0.0.1:18792:127.0.0.1:18792", "home-core"],
        "task-feed": [python, str(ROOT / "scripts/openclaw_tasks.py"),
                      "--transport", str(config / "codex-task-transport.json"),
                      "--communication-config", str(config / "communication.json"),
                      "--transport-tokens", str(config / "transport-tokens.json"),
                      "--cursor", str(state / "task-feed.cursor.json"), "--follow"],
        "telegram": [python, str(ROOT / "scripts/openclaw-telegram.py"), "run",
                     "--config", str(config / "telegram.json"),
                     "--tokens", str(config / "telegram-tokens.json"),
                     "--state", str(state / "telegram"), "--supervised"],
    }
    return {name: {"Label": PREFIX + name, "ProgramArguments": command,
                   "WorkingDirectory": str(ROOT), "RunAtLoad": True,
                   "KeepAlive": {"SuccessfulExit": False} if name == "telegram" else True,
                   "ThrottleInterval": 15, "ProcessType": "Background", "Umask": 0o077,
                   "EnvironmentVariables": {"HOME": str(home),
                                            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
                   "StandardOutPath": str(state / (name + ".log")),
                   "StandardErrorPath": str(state / (name + ".log"))}
            for name, command in commands.items()}


def launchctl(*arguments: str, required: bool = True) -> None:
    result = subprocess.run(["/bin/launchctl", *arguments], capture_output=True)
    if required and result.returncode:
        raise RuntimeError("launchctl operation failed: " + " ".join(arguments[:2]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="write and load agents; receiver stays disabled")
    parser.add_argument("--activate-telegram", action="store_true", help="load receiver after native/relay qualification")
    args = parser.parse_args()
    os.umask(0o077)
    home = Path.home()
    python = shutil.which("python3")
    if not python:
        parser.error("python3 is required")
    agents = definitions(home, python)
    if not args.install and not args.activate_telegram:
        print(json.dumps({name: spec["ProgramArguments"] for name, spec in agents.items()}, indent=2))
        return
    directory = home / "Library/LaunchAgents"
    domain = "gui/" + str(os.getuid())
    if args.install:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Do not reset cursor, sessions, credentials, or transport receipts.
        for name, spec in agents.items():
            path = directory / (spec["Label"] + ".plist")
            with path.open("wb") as handle:
                plistlib.dump(spec, handle)
            path.chmod(0o600)
            launchctl("bootout", domain + "/" + spec["Label"], required=False)
            if name == "telegram":
                launchctl("disable", domain + "/" + spec["Label"])
            else:
                launchctl("enable", domain + "/" + spec["Label"])
                launchctl("bootstrap", domain, str(path))
            print(name + (": staged, disabled" if name == "telegram" else ": supervised"))
    if args.activate_telegram:
        state = home / ".local/state/homecompute/openclaw-communication/telegram"
        cursor = json.loads((state / "telegram-cursor.json").read_text())
        if cursor["paused"] or (state / "telegram-admission.json").exists():
            parser.error("receiver requires reconciliation; preserve cursor and admission guard")
        spec = agents["telegram"]
        launchctl("enable", domain + "/" + spec["Label"])
        launchctl("bootstrap", domain, str(directory / (spec["Label"] + ".plist")))
        print("telegram: supervised, activated")


if __name__ == "__main__":
    main()
