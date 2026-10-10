#!/usr/bin/env python3
"""Install/qualify the disabled Codex worker only in its dedicated Ubuntu VM.

Source bundles are root-owned, immutable public files with an exact SHA-256
manifest. No credential provision, worker activation, model call or host change.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
from typing import Any

MARKER = {"schema_version": 1, "instance_id": "homecompute-codex-v1", "purpose": "codex-worker",
          "work_device": "/dev/vdb", "work_size_gib": 12}
FILES = {"worker.py", "policy.py", "qualify_native.py", "homecompute-codex-worker.service",
         "package.json", "package-lock.json", "bwrap-userns-restrict"}
ROOT = Path("/srv/codex-work")
CONFIG = Path("/etc/homecompute-codex")
SOURCE = Path("/opt/worker")
STATE = Path("/var/lib/homecompute-codex-bootstrap")
UNIT = "homecompute-codex-worker.service"
PACKAGES = ["bubblewrap", "apparmor", "apparmor-utils", "python3", "git", "ca-certificates",
            "nodejs", "npm", "e2fsprogs", "strace"]
APPARMOR_PROFILE_SOURCE = {
    "commit": "b0eb95457bc2de401920308869d016e696c73664",
    "url": "https://gitlab.com/apparmor/apparmor/-/raw/b0eb95457bc2de401920308869d016e696c73664/profiles/apparmor/profiles/extras/bwrap-userns-restrict",
    "upstream_sha256": "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9",
    "normalized_rules_sha256": "c8e86a653f8dcde1a6a28d210f4002bbf60e5b765acb62200108f447c96cd9ab",
}


class BootstrapError(RuntimeError):
    pass


def validate_host(hostname: str, release: dict[str, str], instance: str, marker: Any,
                  manufacturer: str) -> None:
    if hostname != "codex-worker" or release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise BootstrapError("requires dedicated codex-worker Ubuntu 24.04 guest")
    if instance != MARKER["instance_id"] or marker != MARKER or manufacturer != "QEMU":
        raise BootstrapError("dedicated VM identity/marker mismatch")


def secure_file(path: Path, owner: int = 0) -> bytes:
    if path.is_symlink():
        raise BootstrapError("source/marker cannot be a symlink")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise BootstrapError("source/marker must be owner-controlled regular files")
    if info.st_size > 2_000_000:
        raise BootstrapError("source/marker exceeds size budget")
    return path.read_bytes()


def guard_guest() -> None:
    if os.geteuid() != 0:
        raise BootstrapError("guest bootstrap requires root")
    marker = json.loads(secure_file(Path("/etc/homecompute-codex-vm.json")))
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    validate_host(socket.gethostname(), release, Path("/var/lib/cloud/data/instance-id").read_text().strip(),
                  marker, Path("/sys/class/dmi/id/sys_vendor").read_text().strip())
    if (CONFIG / "ENABLED").exists():
        raise BootstrapError("worker activation marker present; reconcile and stop before bootstrap")
    if command(["systemctl", "is-active", "--quiet", UNIT], allow_failure=True).returncode == 0:
        raise BootstrapError("worker already active; bootstrap refuses to change its runtime")


def read_bundle(directory: Path, owner: int = 0) -> tuple[dict[str, bytes], dict[str, str]]:
    if directory.is_symlink() or not directory.is_dir():
        raise BootstrapError("source bundle must be a real directory")
    info = directory.stat()
    if info.st_uid != owner or info.st_mode & 0o022:
        raise BootstrapError("source bundle directory must be owner-controlled")
    manifest = json.loads(secure_file(directory / "manifest.json", owner))
    if (set(manifest) != {"schema_version", "files"} or manifest["schema_version"] != 1
            or not isinstance(manifest["files"], dict) or set(manifest["files"]) != FILES):
        raise BootstrapError("source manifest must name the exact reviewed public files")
    payloads = {}
    for name, digest in manifest["files"].items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BootstrapError("invalid source digest")
        payload = secure_file(directory / name, owner)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise BootstrapError("source digest mismatch")
        payloads[name] = payload
    if json.loads(payloads["package.json"]).get("dependencies") != {"@openai/codex": "0.145.0"}:
        raise BootstrapError("only pinned Codex 0.145.0 may be installed")
    locked = json.loads(payloads["package-lock.json"])["packages"]
    if locked["node_modules/@openai/codex"]["version"] != "0.145.0":
        raise BootstrapError("Codex lock version mismatch")
    rules = "\n".join(line.strip() for line in payloads["bwrap-userns-restrict"].decode().splitlines()
                      if line.strip() and not line.strip().startswith("#"))
    if hashlib.sha256(rules.encode()).hexdigest() != APPARMOR_PROFILE_SOURCE["normalized_rules_sha256"]:
        raise BootstrapError("scoped AppArmor rules differ from the pinned upstream source")
    return payloads, manifest["files"]


def command(argv: list[str], *, timeout: int = 60, allow_failure: bool = False,
            cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(STATE),
                   "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive"}
    result = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True, text=True, timeout=timeout)
    if result.returncode and not allow_failure:
        if STATE.exists():
            log = STATE / "last-command.log"
            log.write_text((result.stdout + result.stderr)[-2_000_000:])
            log.chmod(0o600)
        raise BootstrapError("bootstrap step failed: " + argv[0] + "; diagnostics retained privately")
    return result


def validate_disk(info: dict[str, Any]) -> bool:
    if (info.get("path") != "/dev/vdb" or info.get("type") != "disk" or info.get("size") != 12 * 1024 ** 3
            or info.get("children") or any(path not in {None, str(ROOT)} for path in info.get("mountpoints", []))):
        raise BootstrapError("work disk geometry/ownership mismatch; refusing disk modification")
    if not info.get("fstype") and not info.get("label"):
        return True
    if info.get("fstype") == "ext4" and info.get("label") == "HC_CODEX_WORK":
        return False
    raise BootstrapError("work disk already has an unrecognized filesystem; refusing format")


def inspect_disk() -> bool:
    if not stat.S_ISBLK(Path("/dev/vdb").stat().st_mode):
        raise BootstrapError("work disk must be the dedicated virtio block device")
    disks = json.loads(command(["lsblk", "--json", "--bytes", "--output",
                               "PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS", "/dev/vdb"]).stdout)["blockdevices"]
    if len(disks) != 1:
        raise BootstrapError("ambiguous work disk")
    blank = validate_disk(disks[0])
    # lsblk does not report every unfamiliar on-disk signature (for example
    # a RAID member). Formatting is allowed only after wipefs sees no signature.
    signatures = json.loads(command(["wipefs", "--no-act", "--json", "/dev/vdb"]).stdout)["signatures"]
    if blank and signatures:
        raise BootstrapError("work disk contains an unrecognized signature; refusing format")
    if ROOT.is_mount() and command(["findmnt", "--noheadings", "--output", "SOURCE,FSTYPE",
                                   "--mountpoint", str(ROOT)]).stdout.split() != ["/dev/vdb", "ext4"]:
        raise BootstrapError("work path is already mounted from an unexpected device")
    return blank


def ensure_identity() -> pwd.struct_passwd:
    try:
        identity = pwd.getpwnam("codex-controller")
    except KeyError:
        command(["useradd", "--system", "--no-create-home", "--user-group", "--home-dir", "/nonexistent",
                 "--shell", "/usr/sbin/nologin", "codex-controller"])
        identity = pwd.getpwnam("codex-controller")
    if not 0 < identity.pw_uid < 10000 or identity.pw_shell != "/usr/sbin/nologin":
        raise BootstrapError("unexpected controller identity")
    if any(10000 <= user.pw_uid < 60000 for user in pwd.getpwall()):
        raise BootstrapError("job UID range must remain reserved")
    return identity


def repair_interrupted_packages() -> None:
    if not command(["dpkg", "--audit"]).stdout.strip():
        return
    print(json.dumps({"event": "codex_guest_bootstrap", "stage": "reconcile_interrupted_packages"}), flush=True)
    # A provisioning reboot may interrupt dpkg unpack/configuration. This
    # repair occurs only after the dedicated-host and source/disk guards pass;
    # it neither enables the worker nor retries any coding job.
    command(["dpkg", "--configure", "-a"], timeout=300, allow_failure=True)
    status = command(["dpkg-query", "--show", "--showformat=${binary:Package}\t${db:Status-Abbrev}\n"]).stdout
    reinstalls = []
    for line in status.splitlines():
        name, flags = line.split("\t", 1)
        if len(flags) >= 3 and flags[2] == "R":
            if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?", name):
                raise BootstrapError("invalid package recovery identifier")
            reinstalls.append(name)
    if reinstalls:
        command(["apt-get", "install", "--yes", "--no-install-recommends", "--reinstall", *reinstalls], timeout=600)
    command(["apt-get", "install", "--yes", "--fix-broken"], timeout=300)
    if command(["dpkg", "--audit"]).stdout.strip():
        raise BootstrapError("package recovery remains incomplete")


def repair_interrupted_indexes() -> None:
    lists = Path("/var/lib/apt/lists")
    if not any(path.is_file() and "_Packages" in path.name and path.stat().st_size == 0 for path in lists.iterdir()):
        return
    destination = STATE / "apt-lists-before-recovery"
    if destination.exists() or lists.is_symlink():
        raise BootstrapError("APT cache recovery already exists; reconcile before another repair")
    # Preserve the interrupted cache for diagnosis. An unchanged InRelease can
    # otherwise cause apt to retain zero-byte indexes after a VM interruption.
    lists.rename(destination)
    lists.mkdir(mode=0o755)
    lists.chmod(0o755)
    partial = lists / "partial"
    partial.mkdir(mode=0o700)
    os.chown(partial, pwd.getpwnam("_apt").pw_uid, 0)
    print(json.dumps({"event": "codex_guest_bootstrap", "stage": "reconcile_interrupted_indexes"}), flush=True)


def install(payloads: dict[str, bytes], hashes: dict[str, str], blank: bool) -> dict[str, Any]:
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    print(json.dumps({"event": "codex_guest_bootstrap", "stage": "distro_packages"}), flush=True)
    repair_interrupted_indexes()
    command(["apt-get", "-o", "APT::Update::Error-Mode=any", "update"], timeout=300)
    repair_interrupted_packages()
    command(["apt-get", "install", "--yes", "--no-install-recommends", *PACKAGES], timeout=600)
    # Use distro bwrap policy when supplied. Noble currently omits it; Ubuntu
    # recommends the upstream purpose-built profile in that case. Our reviewed
    # ABI4.0 source keeps capability-denying child stacking and the global
    # kernel restriction, and grants no unconfined profile exception.
    profile = Path("/etc/apparmor.d/bwrap")
    distro_profile_present = profile.is_file()
    if not profile.is_file():
        profile = Path("/etc/apparmor.d/homecompute-codex-bwrap")
        if profile.exists() and secure_file(profile) != payloads["bwrap-userns-restrict"]:
            raise BootstrapError("existing scoped bwrap profile differs; reconcile before replacement")
        profile.write_bytes(payloads["bwrap-userns-restrict"])
        profile.chmod(0o644)
    if (Path("/etc/apparmor.d/disable") / profile.name).exists():
        raise BootstrapError("enabled distro AppArmor bwrap profile is required")
    command(["apparmor_parser", "-r", str(profile)])
    restricted = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
    if not restricted.is_file() or restricted.read_text().strip() != "1":
        raise BootstrapError("AppArmor unprivileged user namespace restriction must remain enabled")
    identity = ensure_identity()
    ROOT.mkdir(mode=0o711, exist_ok=True)
    fstab = Path("/etc/fstab")
    lines = fstab.read_text().splitlines()
    entries = [line for line in lines if line.strip() and not line.lstrip().startswith("#")
               and len(line.split()) > 1 and line.split()[1] == str(ROOT)]
    desired = "LABEL=HC_CODEX_WORK /srv/codex-work ext4 defaults,nosuid,nodev 0 2"
    if entries and entries != [desired]:
        raise BootstrapError("unexpected work disk fstab entry")
    # Recheck after package installation and immediately before any format.
    # A disk changed by another operator must never inherit an earlier blank
    # admission. Existing recognized filesystems are preserved on reruns.
    if inspect_disk() != blank:
        raise BootstrapError("work disk changed during bootstrap; refusing format")
    if blank:
        command(["mkfs.ext4", "-L", "HC_CODEX_WORK", "/dev/vdb"], timeout=120)
    if not entries:
        fstab.write_text("\n".join(lines + [desired]) + "\n")
    if not ROOT.is_mount():
        command(["mount", str(ROOT)])
    os.chown(ROOT, identity.pw_uid, identity.pw_gid)
    ROOT.chmod(0o711)
    SOURCE.mkdir(mode=0o755, exist_ok=True)
    if SOURCE.is_symlink():
        raise BootstrapError("worker install directory cannot be a symlink")
    SOURCE.chmod(0o755)
    for name, payload in payloads.items():
        target = SOURCE / name
        if target.is_symlink():
            raise BootstrapError("worker install target cannot be a symlink")
        target.write_bytes(payload)
        target.chmod(0o444)
    print(json.dumps({"event": "codex_guest_bootstrap", "stage": "locked_cli"}), flush=True)
    # Public CLI files must remain readable/executable after the job UID drop.
    # The private bootstrap HOME stays mode0700; npm's public install uses022.
    previous_umask = os.umask(0o022)
    try:
        command(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], timeout=300, cwd=SOURCE)
    finally:
        os.umask(previous_umask)
    version = command([str(SOURCE / "node_modules/.bin/codex"), "--version"]).stdout.strip()
    if version != "codex-cli 0.145.0":
        raise BootstrapError("installed Codex version mismatch")
    CONFIG.mkdir(mode=0o755, exist_ok=True)
    CONFIG.chmod(0o755)
    (CONFIG / "secrets").mkdir(mode=0o700, exist_ok=True)
    policy = CONFIG / "projects.json"
    if not policy.exists():
        policy.write_text('{"schema_version":1,"projects":{}}\n')
        policy.chmod(0o444)
    target_unit = Path("/etc/systemd/system") / UNIT
    target_unit.write_bytes(payloads[UNIT])
    target_unit.chmod(0o644)
    command(["systemd-analyze", "verify", str(target_unit)])
    command(["systemctl", "daemon-reload"])
    packages = command(["dpkg-query", "--show", "--showformat=${Package}=${Version}\n", *PACKAGES]).stdout.splitlines()
    record = {"schema_version": 1, "at": datetime.now(timezone.utc).isoformat(), "event": "codex_guest_installed",
              "instance_id": MARKER["instance_id"], "source_sha256": hashes, "codex": version,
              "distro_packages": packages, "work_disk_gib": 12, "distro_bwrap_profile_sha256":
              hashlib.sha256(profile.read_bytes()).hexdigest(), "apparmor_userns_restriction": 1,
              "bwrap_apparmor_profile": str(profile),
              "distro_bwrap_profile_present": distro_profile_present,
              "scoped_upstream_profile_source": APPARMOR_PROFILE_SOURCE if not distro_profile_present else None,
              "worker_enabled": False, "credential_files_created": False, "paid_api_calls": 0}
    receipt = STATE / "installation.json"
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    receipt.chmod(0o600)
    return record


def qualify() -> dict[str, Any]:
    # Use the installed unit's exact sandbox/resource properties, while omitting
    # LoadCredential, worker.env, startup gates and network access. No broker or
    # model authentication can occur in this synthetic service.
    unit = Path("/etc/systemd/system") / UNIT
    lines = secure_file(unit).decode().splitlines()
    properties = []
    excluded = {"Type", "ExecStart", "ExecStartPre", "EnvironmentFile", "LoadCredential", "Restart", "RestartSec",
                "StandardOutput", "StandardError"}
    environments = []
    in_service = False
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            in_service = line == "[Service]"
        elif in_service and line and not line.startswith("#"):
            key = line.split("=", 1)[0]
            if key == "Environment":
                environments.append(line.split("=", 1)[1])
            elif key not in excluded:
                properties += ["--property=" + line]
    properties.append("--property=Environment=" + " ".join(environments))
    result = command(["systemd-run", "--unit=homecompute-codex-qualification", "--wait", "--collect", "--pipe",
                      "--property=PrivateNetwork=yes", *properties,
                      "/usr/bin/python3", str(SOURCE / "qualify_native.py")], timeout=90)
    receipts = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"event":')]
    if not receipts or receipts[-1].get("event") != "native_worker_synthetic_qualification":
        raise BootstrapError("synthetic qualification receipt missing")
    record = {"schema_version": 1, "at": datetime.now(timezone.utc).isoformat(),
              "instance_id": MARKER["instance_id"], "qualification": receipts[-1],
              "worker_enabled": False, "production_network_qualified": False, "paid_api_calls": 0}
    receipt = STATE / "qualification.json"
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    receipt.chmod(0o600)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "install", "qualify"])
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    guard_guest()
    payloads, hashes = read_bundle(args.source)
    blank = inspect_disk()
    if args.action == "check":
        record = {"event": "codex_guest_bootstrap_ready", "disk_blank": blank, "source_sha256": hashes,
                  "worker_enabled": False, "credentials_accessed": False}
    elif args.action == "install":
        record = install(payloads, hashes, blank)
    else:
        record = qualify()
    print(json.dumps(record), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (BootstrapError, ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"event": "codex_guest_bootstrap_failed", "reason": str(error)}), flush=True)
        raise SystemExit(1)
