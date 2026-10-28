#!/usr/bin/env python3
"""Root-only, exact-approved Homepage image maintenance; no agent Docker access."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Any, Callable, Iterator

from docker_image_updates import Registry, reference

CONTAINER = "homecompute-homepage-homepage-1"
REPOSITORY = "ghcr.io/gethomepage/homepage"
ID = re.compile(r"[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")
ENV = {"PATH": "/run/current-system/sw/bin:/usr/bin:/bin", "HOME": "/var/empty", "LANG": "C.UTF-8"}


class Denied(ValueError):
    """A gate failed; effects must stop."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Paths:
    release: Path = Path("/srv/homecompute/current")
    environment: Path = Path("/etc/homecompute/homepage.env")
    candidate: Path = Path("/etc/homecompute/homepage-maintenance-candidate.json")
    state: Path = Path("/var/lib/homecompute/homepage-maintenance")
    deployment_lock: Path = Path("/var/lib/homecompute/deploy.lock")


class Maintenance:
    def __init__(self, paths: Paths = Paths(), runner: Callable[..., Any] = subprocess.run,
                 registry: Registry | None = None, health: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.time, uid: int = 0):
        self.paths, self.runner, self.registry = paths, runner, registry or Registry()
        self.health, self.clock, self.uid = health or self.http_health, clock, uid

    def trusted(self, path: Path) -> Path:
        resolved = path.resolve(strict=True)
        for part in [resolved, *resolved.parents]:
            item = part.stat()
            if item.st_uid != self.uid or item.st_mode & 0o022:
                raise Denied("deployment inputs must be owned by the operator and not writable by others")
        return resolved

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.paths.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.trusted(self.paths.state)
        with self.paths.deployment_lock.open("a") as handle:
            self.trusted(self.paths.deployment_lock)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Denied("another deployment is running") from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def run(self, argv: list[str], timeout: int = 30) -> str:
        result = self.runner(argv, capture_output=True, text=True, timeout=timeout, env=ENV, cwd="/")
        if result.returncode or len(result.stdout) > 1048576:
            raise Denied("fixed maintenance command failed or exceeded its output budget")
        return result.stdout

    def config_overlay(self) -> tuple[Path, Path] | None:
        """Accept only the fixed, read-only services file over the release config."""
        manifest = self.paths.state / "active-config.json"
        if not manifest.exists() and not manifest.is_symlink():
            return None
        services = self.paths.state / "services.yaml"
        for path in (manifest, services):
            if path.is_symlink() or not path.is_file():
                raise Denied("Homepage config overlay must use fixed regular files")
            self.trusted(path)
        expected = {"services": {"homepage": {"volumes": [{"type": "bind", "source": str(services),
                    "target": "/app/config/services.yaml", "read_only": True}]}}}
        if manifest.stat().st_size > 4096 or digest(json.loads(manifest.read_text())) != digest(expected):
            raise Denied("Homepage config overlay differs from the fixed read-only services mount")
        return self.trusted(manifest), self.trusted(services)

    def compose(self, override: Path | None = None) -> list[str]:
        directory = self.trusted(self.paths.release) / "deploy/homepage"
        args = ["docker", "compose", "--project-name", "homecompute-homepage", "--project-directory", str(directory),
                "--env-file", str(self.trusted(self.paths.environment)), "-f", str(self.trusted(directory / "compose.yaml"))]
        overlay = self.config_overlay()
        if overlay:
            args += ["-f", str(overlay[0])]
        active = self.paths.state / "active-image.json"
        if active.exists():
            args += ["-f", str(self.trusted(active))]
        if override:
            args += ["-f", str(self.trusted(override))]
        return args

    def inspect(self, kind: str, target: str) -> dict[str, Any]:
        args = ["docker", "inspect", target] if kind == "container" else ["docker", "image", "inspect", target]
        items = json.loads(self.run(args))
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise Denied("invalid Docker metadata")
        return items[0]

    @staticmethod
    def pin(value: str) -> str:
        parsed = reference(value)
        if (parsed["registry"] != "ghcr.io" or parsed["repository"] != "gethomepage/homepage" or not parsed["digest"]):
            raise Denied("only immutable official Homepage images are allowed")
        return REPOSITORY + "@" + str(parsed["digest"])

    @staticmethod
    def runtime(container: dict[str, Any]) -> str:
        # Image-inherited NODE_VERSION, command, and working directory may change.
        # Bind the fields explicitly set by the reviewed Compose service instead.
        config = {key: container["Config"].get(key) for key in ("User", "Healthcheck")}
        environment = [value for value in container["Config"].get("Env", []) if value.startswith("HOMEPAGE_ALLOWED_HOSTS=")]
        if len(environment) != 1:
            raise Denied("Homepage allowed-host environment must be explicit and unique")
        config["allowed_hosts"] = environment
        host = {key: value for key, value in container["HostConfig"].items() if key not in {"ConsoleSize"}}
        return digest({"config": config, "host": host, "mounts": container["Mounts"]})

    def source(self) -> dict[str, str]:
        directory = self.trusted(self.paths.release) / "deploy/homepage"
        files = [self.trusted(directory / "compose.yaml"), self.trusted(self.paths.environment)]
        overlay = self.config_overlay()
        if overlay:
            files.extend(overlay)
        active = self.paths.state / "active-image.json"
        if active.exists():
            files.append(self.trusted(active))
        self.trusted(directory / "config")
        for path in sorted((directory / "config").rglob("*")):
            if path.is_symlink():
                raise Denied("Homepage configuration symlinks are unsupported")
            if path.is_file():
                files.append(self.trusted(path))
        if len(files) > 128 or sum(path.stat().st_size for path in files) > 2097152:
            raise Denied("Homepage configuration exceeds backup budget")
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}

    def baseline(self, require_health: bool = True) -> dict[str, Any]:
        container = self.inspect("container", CONTAINER)
        labels = container["Config"].get("Labels", {})
        if (container.get("Name") != "/" + CONTAINER or labels.get("com.docker.compose.project") != "homecompute-homepage"
                or labels.get("com.docker.compose.service") != "homepage"):
            raise Denied("container does not belong to the fixed Homepage service")
        config = json.loads(self.run(self.compose() + ["config", "--format", "json"]))
        if set(config["services"]) != {"homepage"}:
            raise Denied("Homepage Compose must contain exactly the reviewed service")
        service = config["services"]["homepage"]
        pin = self.pin(container["Config"]["Image"])
        expected_hash = self.run(self.compose() + ["config", "--hash", "homepage"]).strip().split()[-1]
        if self.pin(service["image"]) != pin or labels.get("com.docker.compose.config-hash") != expected_hash:
            raise Denied("running Homepage differs from trusted Compose configuration")
        mounts = container["Mounts"]
        binds = [mount for mount in mounts if mount.get("Type") == "bind"]
        expected_binds = {"/app/config": str(self.trusted(self.paths.release) / "deploy/homepage/config")}
        overlay = self.config_overlay()
        if overlay:
            expected_binds["/app/config/services.yaml"] = str(overlay[1])
        if (not service.get("read_only") or service.get("privileged") or "build" in service
                or service.get("user") != "1000:1000" or container["HostConfig"].get("Privileged")
                or container["HostConfig"].get("ReadonlyRootfs") is not True
                or len(binds) != len(expected_binds) or {mount.get("Destination") for mount in binds} != set(expected_binds)
                or any(mount.get("RW") is not False or mount.get("Source") != expected_binds.get(mount.get("Destination")) for mount in binds)
                or any(mount.get("Type") not in {"bind", "tmpfs"} or mount.get("Type") == "tmpfs"
                       and mount.get("Destination") not in {"/tmp", "/app/.next/cache", "/app/config/logs"} for mount in mounts)):
            raise Denied("only the reviewed stateless Homepage configuration supports image rollback")
        if require_health and (not container["State"].get("Running") or container["State"].get("Health", {}).get("Status") != "healthy" or not self.health()):
            raise Denied("Homepage must be healthy before maintenance")
        image = self.inspect("image", container["Image"])
        if pin not in [self.pin(item) for item in image.get("RepoDigests", [])]:
            raise Denied("running image lacks exact registry digest provenance")
        platform = self.registry.platform(reference(pin), pin.split("@", 1)[1], image["Os"], image["Architecture"], image.get("Variant", ""))
        if container["Image"] not in set(platform.values()):
            raise Denied("running image identity differs from verified registry manifest")
        return {"container_id": container["Id"], "image": pin, "image_id": container["Image"], "platform": platform,
                "os": image["Os"], "architecture": image["Architecture"], "variant": image.get("Variant", ""),
                "runtime_hash": self.runtime(container), "compose_hash": expected_hash, "source_hashes": self.source()}

    def write(self, path: Path, value: Any) -> None:
        temp = path.with_suffix(".tmp")
        with temp.open("w") as handle:
            os.chmod(temp, 0o600)
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def record(self, job: dict[str, Any], event: str, **fields: Any) -> None:
        job["events"].append({"event": event, "time": self.clock(), **fields})
        job["status"] = event
        self.write(self.paths.state / (job["id"] + ".json"), job)

    def load(self, proposal_id: str) -> dict[str, Any]:
        if not isinstance(proposal_id, str) or not ID.fullmatch(proposal_id):
            raise Denied("invalid proposal identifier")
        job = json.loads(self.trusted(self.paths.state / (proposal_id + ".json")).read_text())
        if job["id"] != proposal_id or digest(job["proposal"]) != job["proposal_hash"]:
            raise Denied("proposal has changed")
        return job

    def prepare(self) -> dict[str, Any]:
        with self.locked():
            pending = []
            for path in self.paths.state.glob("*.json"):
                if ID.fullmatch(path.stem):
                    job = self.load(path.stem)
                    if job["status"] in {"attempt_started", "recreate_started", "rollback_started", "reconciliation_required"}:
                        raise Denied("an uncertain previous attempt requires human reconciliation")
                    pending.append(job)
            if len(pending) >= 64:
                raise Denied("maintenance history needs operator archival")
            manifest = json.loads(self.trusted(self.paths.candidate).read_text())
            if (set(manifest) != {"schema_version", "candidate_image", "release_notes", "compatibility_review", "rollback_supported"}
                    or manifest["schema_version"] != 1 or manifest["rollback_supported"] is not True
                    or any(not isinstance(manifest[key], str) or not 1 <= len(manifest[key]) <= 4096
                           for key in ("release_notes", "compatibility_review"))):
                raise Denied("candidate needs a reviewed release and explicit stateless rollback assessment")
            candidate = self.pin(manifest["candidate_image"])
            baseline = self.baseline()
            for job in pending:
                existing = job["proposal"]
                if (job["status"] in {"prepared", "approved"} and existing["expires_at"] > self.clock()
                        and existing["baseline"] == baseline and existing["candidate_manifest_hash"] == digest(manifest)
                        and (job["status"] != "approved" or job["events"][-1]["expires_at"] > self.clock())):
                    return job
            verified = self.registry.platform(reference(candidate), candidate.split("@", 1)[1],
                                              baseline["os"], baseline["architecture"], baseline["variant"])
            if verified["platform_digest"] == baseline["platform"]["platform_digest"]:
                raise Denied("candidate does not change the deployed platform image")
            proposal = {"schema_version": 1, "target": "home-core", "service": "homepage", "baseline": baseline,
                        "candidate_image": candidate, "candidate_platform": verified, "review": manifest,
                        "candidate_manifest_hash": digest(manifest), "expires_at": self.clock() + 3600,
                        "effects": ["pull exact Homepage digest", "recreate Homepage only", "HTTP and Docker health verification"],
                        "rollback": "retain previous local image; restore image only when source and runtime configuration match"}
            job = {"id": os.urandom(16).hex(), "proposal": proposal, "proposal_hash": digest(proposal), "events": []}
            self.record(job, "prepared")
            return job

    def review(self, proposal_id: str) -> dict[str, Any]:
        return self.load(proposal_id)

    def approve(self, proposal_id: str, proposal_hash: str) -> dict[str, Any]:
        with self.locked():
            job = self.load(proposal_id)
            self.match(job, proposal_hash)
            if job["status"] != "prepared":
                raise Denied("only a prepared job can receive human approval")
            self.record(job, "approved", proposal_hash=proposal_hash, expires_at=self.clock() + 300)
            return job

    def match(self, job: dict[str, Any], proposal_hash: str) -> None:
        if (not isinstance(proposal_hash, str) or not HASH.fullmatch(proposal_hash)
                or proposal_hash != job["proposal_hash"] or self.clock() > job["proposal"]["expires_at"]):
            raise Denied("exact proposal approval is missing or stale")

    def checked(self, job: dict[str, Any]) -> None:
        proposal = job["proposal"]
        if digest(json.loads(self.trusted(self.paths.candidate).read_text())) != proposal["candidate_manifest_hash"]:
            raise Denied("reviewed candidate manifest changed")
        if self.baseline() != proposal["baseline"]:
            raise Denied("deployed baseline changed; prepare a new proposal")

    def effect_fresh(self, job: dict[str, Any]) -> None:
        self.match(job, job["proposal_hash"])
        approvals = [event for event in job["events"] if event["event"] == "approved"]
        if len(approvals) != 1 or self.clock() > approvals[0]["expires_at"]:
            raise Denied("human approval expired before the next production effect")

    def backup(self, job: dict[str, Any]) -> Path:
        directory = self.paths.state / (job["id"] + "-backup")
        directory.mkdir(mode=0o700)
        entries = []
        for number, (name, checksum) in enumerate(job["proposal"]["baseline"]["source_hashes"].items()):
            content = self.trusted(Path(name)).read_bytes()
            if hashlib.sha256(content).hexdigest() != checksum:
                raise Denied("configuration changed while backing up")
            destination = directory / str(number)
            destination.write_bytes(content); destination.chmod(0o600)
            entries.append({"source": name, "sha256": checksum, "backup": str(destination)})
        self.write(directory / "manifest.json", {"source_files": entries, "previous_image": job["proposal"]["baseline"]["image"],
                                                 "previous_image_id": job["proposal"]["baseline"]["image_id"], "proposal_hash": job["proposal_hash"]})
        return directory

    def healthy(self, expected: str, runtime_hash: str) -> bool:
        container = self.inspect("container", CONTAINER)
        return (self.pin(container["Config"]["Image"]) == expected and self.runtime(container) == runtime_hash
                and container["State"].get("Running") is True and container["State"].get("Health", {}).get("Status") == "healthy" and self.health())

    def execute(self, proposal_id: str, proposal_hash: str) -> dict[str, Any]:
        with self.locked():
            job = self.load(proposal_id); self.match(job, proposal_hash)
            if job["status"] != "approved" or self.clock() > job["events"][-1]["expires_at"]:
                raise Denied("human approval is missing, consumed, or stale")
            self.checked(job)
            self.record(job, "attempt_started")  # Durable single-attempt claim precedes every effect.
            proposal, stage = job["proposal"], "backup"
            override = self.paths.state / (proposal_id + "-image.json")
            try:
                backup = self.backup(job)
                self.write(override, {"services": {"homepage": {"image": proposal["candidate_image"]}}})
                stage = "pull"
                self.effect_fresh(job)
                self.run(self.compose(override) + ["pull", "homepage"], 240)
                image = self.inspect("image", proposal["candidate_image"])
                if (image["Id"] not in set(proposal["candidate_platform"].values())
                        or proposal["candidate_image"] not in [self.pin(item) for item in image.get("RepoDigests", [])]
                        or image["Os"] != proposal["baseline"]["os"] or image["Architecture"] != proposal["baseline"]["architecture"]):
                    raise Denied("pulled image identity differs from approved manifest")
                self.checked(job)  # Recheck after pull, immediately before container replacement.
                self.effect_fresh(job)
                stage = "recreate"
                self.record(job, "recreate_started", backup=str(backup))
                self.run(self.compose(override) + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180", "homepage"], 210)
                if (self.source() != proposal["baseline"]["source_hashes"]
                        or not self.healthy(proposal["candidate_image"], proposal["baseline"]["runtime_hash"])):
                    raise Denied("Homepage health or runtime configuration verification failed")
                self.write(self.paths.state / "active-image.json", {"services": {"homepage": {"image": proposal["candidate_image"]}}})
                self.record(job, "succeeded")
            except (ValueError, KeyError, OSError, subprocess.SubprocessError, http.client.HTTPException):
                self.record(job, "reconciliation_required" if stage == "recreate" else "failed_before_recreate", stage=stage)
                if stage == "recreate":
                    self.rollback(job, override)
            return job

    def rollback(self, job: dict[str, Any], override: Path) -> None:
        proposal, previous = job["proposal"], job["proposal"]["baseline"]
        try:
            container = self.inspect("container", CONTAINER)
            if (self.source() != previous["source_hashes"] or self.runtime(container) != previous["runtime_hash"]
                    or self.pin(container["Config"]["Image"]) not in {previous["image"], proposal["candidate_image"]}
                    or container["Image"] not in set(proposal["candidate_platform"].values()) | {previous["image_id"]}
                    or self.inspect("image", previous["image"])["Id"] != previous["image_id"]):
                return  # Ambiguous state is preserved for the human operator.
            self.record(job, "rollback_started")
            self.write(override, {"services": {"homepage": {"image": previous["image"]}}})
            self.run(self.compose(override) + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180", "homepage"], 210)
            if not self.healthy(previous["image"], previous["runtime_hash"]):
                raise Denied("rollback health failed")
            self.record(job, "rolled_back")
        except (ValueError, KeyError, OSError, subprocess.SubprocessError, http.client.HTTPException):
            self.record(job, "reconciliation_required", stage="rollback")

    @staticmethod
    def http_health() -> bool:
        connection = http.client.HTTPConnection("127.0.0.1", 80, timeout=5)
        try:
            connection.request("GET", "/api/healthcheck", headers={"Host": "home-core"})
            result = connection.getresponse(); body = result.read(65537)
            if result.status != 200 or len(body) > 65536 or body.strip() != b"up":
                return False
            connection.close()
            connection.request("GET", "/", headers={"Host": "home-core"})
            result = connection.getresponse(); body = result.read(65536).lower()
            return result.status == 200 and b"<html" in body
        except (ValueError, OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "review", "approve", "execute", "status"])
    parser.add_argument("proposal_id", nargs="?"); parser.add_argument("proposal_hash", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != "home-core":
        parser.error("run through the trusted root operator on home-core")
    executor = Maintenance()
    try:
        if args.operation == "prepare" and args.proposal_id is None and args.proposal_hash is None:
            result = executor.prepare()
        elif args.operation in {"review", "status"} and args.proposal_id and args.proposal_hash is None:
            result = executor.review(args.proposal_id)
        elif args.operation in {"approve", "execute"} and args.proposal_id and args.proposal_hash:
            result = getattr(executor, args.operation)(args.proposal_id, args.proposal_hash)
        else:
            raise Denied("operation arguments do not match the closed interface")
        print(json.dumps(result, indent=2)); return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "denied", "reason": str(error) if isinstance(error, Denied) else "maintenance inputs unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
