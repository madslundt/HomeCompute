#!/usr/bin/env python3
"""Create persistent private credentials for the PodWave Caddy site."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path


ENV_FILE = Path("/srv/state/podwave-auth/auth.env")
PASSWORD_FILE = Path("/srv/state/podwave-auth/password.txt")
CONTROL_PLANE_ENV = Path("/etc/homecompute/control-plane.env")


def control_plane_value(name: str) -> str:
    for line in CONTROL_PLANE_ENV.read_text().splitlines():
        if line.startswith(name + "="):
            return line.partition("=")[2]
    raise SystemExit(f"Missing {name} in {CONTROL_PLANE_ENV}")


def atomic_secret(path: Path, value: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def private_root_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o600


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate", action="store_true", help="replace the generated proxy login")
    args = parser.parse_args()
    if not args.rotate and (ENV_FILE.exists() or PASSWORD_FILE.exists()):
        if not (private_root_file(ENV_FILE) and private_root_file(PASSWORD_FILE)):
            raise SystemExit("Partial PodWave credentials found; preserving them for operator recovery.")
        match = re.fullmatch(r"PODWAVE_AUTH_HASH='(\S+)'\n?", ENV_FILE.read_text())
        if not match or not PASSWORD_FILE.read_text().strip():
            raise SystemExit("PodWave credential files are malformed; preserving them for operator recovery.")
        return

    password = secrets.token_urlsafe(32)
    image = control_plane_value("CADDY_IMAGE")
    result = subprocess.run(
        ["docker", "run", "--rm", image, "hash-password", "--plaintext", password],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    password_hash = result.stdout.strip()
    if not password_hash.startswith("$2a$") and not password_hash.startswith("$2b$"):
        raise SystemExit("Caddy returned an unexpected password hash; no credentials were written.")
    atomic_secret(ENV_FILE, f"PODWAVE_AUTH_HASH='{password_hash}'\n")
    atomic_secret(PASSWORD_FILE, password + "\n")
    print("Created PodWave login credentials; retrieve the password with:")
    print(f"  sudo cat {PASSWORD_FILE}")


if __name__ == "__main__":
    main()
