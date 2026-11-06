#!/usr/bin/env python3
"""Source-owned isolated reference diagnostic; no production routing or model provisioning."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from whisper_reference_guard import (
    LIMIT,
    Guard,
    Refused,
    command,
    memory,
    private_directory,
    read_json,
    same_trial,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deploy/whisper-reference-shadow"
STATE = Path.home() / ".local/share/homecompute/whisper-reference-shadow"
MODEL = "/srv/gb10-ai/cache/whisper-cpp/ggml-large-v3.bin"
OWNER_LABEL = "io.homecompute.whisper-reference.owner"
SOURCE_LABEL = "io.homecompute.source-revision"
IMAGE_PATTERN = r"sha256:[0-9a-f]{64}"
BRANCH = "refs/heads/codex/whisper-reference-shadow"
TMPFS = "/tmp:rw,nosuid,nodev,noexec,size=67108864,uid=10001,gid=10001,mode=0700"
BOOTSTRAP = """import os,secrets,time
from pathlib import Path
fd=os.open('/tmp/shadow.key',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
os.write(fd,secrets.token_hex(32).encode());os.close(fd)
end=time.monotonic()+300
while not Path('/tmp/load-enabled').is_file():
 if time.monotonic()>=end:raise SystemExit(1)
 time.sleep(.1)
os.execvp('python3',['python3','-I','-B','/opt/homecompute/adapter.py'])
"""
HEALTH_PROGRAM = """import urllib.request,urllib.error
try:r=urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)
except urllib.error.HTTPError as error:
 if error.code!=503:raise SystemExit(1)
 r=error
with r:
 data=r.read(16385)
 if len(data)>16384:raise SystemExit(1)
 print(data.decode())
"""



def manifest():
    value = json.loads((SOURCE / "model-pin.json").read_text())
    patterns = {"model_sha256": r"[0-9a-f]{64}", "model_snapshot": r"[0-9a-f]{40}",
                "runtime_commit": r"[0-9a-f]{40}", "loader_sha256": r"[0-9a-f]{64}",
                "base_arm64_image": IMAGE_PATTERN}
    if (value.get("schema_version") != 1 or value.get("runtime") != "openai-whisper-reference"
            or not re.fullmatch(r"nvcr.io/nvidia/pytorch@sha256:[0-9a-f]{64}", value.get("base_manifest", ""))
            or not isinstance(value.get("loader_version"), str) or not value["loader_version"]
            or not isinstance(value.get("decoding_defaults"), dict)
            or any(not re.fullmatch(pattern, value.get(key, "")) for key, pattern in patterns.items())):
        raise Refused("Invalid source model/runtime pin")
    return value


def published_source(revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision or ""):
        raise Refused("A full published source revision is required")
    git = ["git", "-C", str(ROOT)]
    if command(git + ["rev-parse", "HEAD"]) != revision or command(git + ["status", "--porcelain"]):
        raise Refused("Use the clean checkout of the requested source revision")
    advertised = command(git + ["ls-remote", "https://github.com/madslundt/HomeCompute.git", BRANCH])
    if advertised.split() != [revision, BRANCH]:
        raise Refused("Source must be the published diagnostic branch head")


def expected_labels(revision, model):
    prefix = "io.homecompute.whisper-reference."
    return {SOURCE_LABEL: revision, "org.opencontainers.image.revision": revision,
            prefix + "scope": "isolated-shadow-only", prefix + "model-sha256": model["model_sha256"],
            prefix + "model-snapshot": model["model_snapshot"], prefix + "runtime-commit": model["runtime_commit"],
            prefix + "loader-sha256": model["loader_sha256"]}


class Runtime:
    def __init__(self, state=STATE):
        self.state = Path(state)
        self.guard = Guard(self, self.state)

    def source_digest(self):
        paths = [Path(__file__), Path(__file__).with_name("whisper_reference_guard.py"), SOURCE / "model-pin.json"]
        return hashlib.sha256(b"".join(hashlib.sha256(path.read_bytes()).digest() for path in paths)).hexdigest()

    def no_active(self):
        path = self.state / "active.json"
        if path.exists() or path.is_symlink():
            raise Refused("An active journal exists; inspect and stop that trial first")

    def verify_image(self, image_id, revision, model):
        if not re.fullmatch(IMAGE_PATTERN, image_id or ""):
            raise Refused("An immutable image ID is required")
        value = json.loads(command(["docker", "image", "inspect", image_id]))[0]
        labels = value["Config"].get("Labels") or {}
        if (value["Id"] != image_id or value["Architecture"] != "arm64"
                or value["Config"]["User"] != "10001:10001"
                or any(labels.get(key) != expected for key, expected in expected_labels(revision, model).items())):
            raise Refused("Actual image does not match the pinned source/model/runtime")
        return value

    def build(self, revision):
        self.no_active()  # Never occupy the lifecycle lock while a guardian needs it.
        published_source(revision)
        model = manifest()
        before = memory(loading=True)
        base = json.loads(command(["docker", "image", "inspect", model["base_manifest"]]))[0]
        if base["Id"] != model["base_arm64_image"] or base["Architecture"] != "arm64":
            raise Refused("Exact pinned ARM64 base must already be cached")
        tag = "homecompute/whisper-reference-shadow:" + revision
        log_path = self.state / ("build-" + revision + "-" + secrets.token_hex(4) + ".log")
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as log:
            command(["docker", "build", "--platform", "linux/arm64", "--pull=false", "--memory", str(LIMIT),
                     "--memory-swap", str(LIMIT), "--cpu-period", "100000", "--cpu-quota", "400000",
                     "--build-arg", "HOME_COMPUTE_REVISION=" + revision, "-t", tag, str(SOURCE)],
                    timeout=3600, log=log, env={**os.environ, "DOCKER_BUILDKIT": "0"})
        image_id = json.loads(command(["docker", "image", "inspect", tag]))[0]["Id"]
        self.verify_image(image_id, revision, model)
        return {"source_revision": revision, "image_id": image_id, "memory_before": before, "build_log": log_path.name}

    def environment(self, record):
        return {"SHADOW_MODEL_FILE": "/models/ggml-large-v3.bin", "SHADOW_KEY_FILE": "/tmp/shadow.key",
                "SHADOW_IMAGE_ID": record["image_id"], "NVIDIA_DRIVER_CAPABILITIES": "compute,utility"}

    def run_arguments(self, record):
        args = ["docker", "run", "--detach", "--name", record["name"], "--label", OWNER_LABEL + "=" + record["owner"],
                "--init", "--network", "none", "--read-only", "--user", "10001:10001", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", str(LIMIT), "--memory-swap", str(LIMIT),
                "--cpus", "4", "--pids-limit", "128", "--shm-size", "64m", "--log-driver", "none", "--restart", "no",
                "--gpus", "device=0", "--tmpfs", TMPFS,
                "--mount", "type=bind,src=" + MODEL + ",dst=/models/ggml-large-v3.bin,readonly", "--entrypoint", "python3"]
        for key, value in self.environment(record).items():
            args += ["--env", key + "=" + value]
        return args + [record["image_id"], "-I", "-B", "-c", BOOTSTRAP]

    def owned_container(self, record):
        owner = record.get("owner", "")
        cid = record.get("container_id")
        if (not re.fullmatch(r"[0-9a-f]{24}", owner) or record.get("name") != "hc-whisper-reference-" + owner
                or (cid and not re.fullmatch(r"[0-9a-f]{64}", cid))
                or not re.fullmatch(IMAGE_PATTERN, record.get("image_id", ""))
                or not re.fullmatch(r"[0-9a-f]{40}", record.get("source_revision", ""))):
            raise Refused("Invalid diagnostic ownership journal")
        value = json.loads(command(["docker", "container", "inspect", cid or record["name"]]))[0]
        host = value["HostConfig"]
        mounts = [item for item in value.get("Mounts", []) if item["Type"] != "tmpfs"]
        labels = value["Config"].get("Labels") or {}
        gpu = host.get("DeviceRequests") or []
        env = dict(item.split("=", 1) for item in value["Config"].get("Env", []))
        if (value["Name"] != "/" + record["name"] or value["Image"] != record["image_id"]
                or (cid and value["Id"] != cid) or labels.get(OWNER_LABEL) != owner
                or any(labels.get(key) != item for key, item in expected_labels(record["source_revision"], record["model_pin"]).items())
                or value["Config"]["User"] != "10001:10001" or host["NetworkMode"] != "none"
                or value["Config"].get("Entrypoint") != ["python3"]
                or value["Config"].get("Cmd") != ["-I", "-B", "-c", BOOTSTRAP]
                or any(env.get(key) != item for key, item in self.environment(record).items())
                or host.get("Init") is not True or host.get("ShmSize") != 67108864
                or host.get("CapAdd") or host.get("Devices")
                or host.get("Tmpfs") != {"/tmp": TMPFS.split(":", 1)[1]}
                or host.get("Memory") != LIMIT or host.get("MemorySwap") != LIMIT or host.get("NanoCpus") != 4000000000
                or host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False
                or host.get("PidsLimit") != 128 or host.get("LogConfig", {}).get("Type") != "none"
                or host.get("PortBindings") or "ALL" not in (host.get("CapDrop") or [])
                or "no-new-privileges" not in (host.get("SecurityOpt") or [])
                or host.get("RestartPolicy", {}).get("Name") != "no"
                or len(gpu) != 1 or gpu[0].get("DeviceIDs") != ["0"] or ["gpu"] not in gpu[0].get("Capabilities", [])
                or len(mounts) != 1 or mounts[0].get("Type") != "bind" or mounts[0].get("Source") != MODEL
                or mounts[0].get("Destination") != "/models/ggml-large-v3.bin" or mounts[0].get("RW") is not False):
            raise Refused("Container ownership, immutable identity or isolation differs")
        return value

    def health(self, record, *, require_ready=True):
        self.owned_container(record)
        value = json.loads(command(["docker", "exec", record["container_id"], "python3", "-I", "-B", "-c", HEALTH_PROGRAM], timeout=8))
        model = record["model_pin"]
        expected = {key: model[key] for key in ("model_sha256", "model_snapshot", "runtime", "runtime_commit", "loader_version", "loader_sha256", "decoding_defaults")}
        expected.update(image_id=record["image_id"], homecompute_revision=record["source_revision"], device="cuda")
        if (value.get("status") not in (("ok",) if require_ready else ("ok", "unavailable"))
                or type(value.get("inference_busy")) is not bool
                or any(value.get(key) != item for key, item in expected.items())
                or value.get("max_audio_seconds") != 30 or value.get("max_upload_bytes") != 1000000
                or type(value.get("gpu_model_buffer_bytes")) is not int or value["gpu_model_buffer_bytes"] <= 0):
            raise Refused("Actual reference health identity differs or idle state is unknown")
        return value

    def cgroup_snapshot(self, actual):
        """Read bounded kernel metadata directly; never wait for a blocked container exec."""
        try:
            pid = actual['State']['Pid']
            if type(pid) is not int or pid <= 0:
                raise Refused("No live container process")
            text = Path('/proc', str(pid), 'cgroup').read_text()
            if len(text) > 4096:
                raise Refused("Unexpected cgroup metadata")
            rows = [line[3:] for line in text.splitlines() if line.startswith('0::/')]
            if len(rows) != 1 or actual['Id'] not in rows[0] or '..' in Path(rows[0]).parts:
                raise Refused("Container cgroup identity differs")
            root = Path('/sys/fs/cgroup') / rows[0].lstrip('/')
            names = ('memory.current', 'memory.peak', 'memory.max', 'memory.events', 'memory.swap.current', 'memory.swap.max', 'memory.stat')
            values = {}
            for name in names:
                with (root / name).open() as stream:
                    data = stream.read(16385)
                if len(data) > 16384:
                    raise Refused("Unexpected cgroup counter size")
                values[name] = data
            stats = dict(line.split() for line in values['memory.stat'].splitlines())
            values['memory.stat'] = {key: stats[key] for key in ('anon', 'file', 'kernel', 'pagetables', 'slab') if key in stats}
            return {'status': 'observed', 'counters': values}
        except (OSError, KeyError, ValueError, Refused):
            return {'status': 'unverified', 'reason': 'bounded_host_cgroup_read_unavailable'}

    def resource(self, record):
        actual = self.owned_container(record)
        return {"container_id": actual["Id"], "image_id": actual["Image"], "running": actual["State"]["Running"],
                "oom_killed": actual["State"]["OOMKilled"], "exit_code": actual["State"]["ExitCode"],
                "restart_count": actual["RestartCount"], "cgroup": self.cgroup_snapshot(actual),
                "memory": self.guard.sample(record)}

    def start(self, revision, image_id):
        self.no_active()
        published_source(revision)
        model = manifest()
        self.verify_image(image_id, revision, model)
        before = memory(loading=True)
        if not Path(MODEL).is_file() or Path(MODEL).is_symlink():
            raise Refused("Approved existing model must be a regular file")
        owner = secrets.token_hex(12)
        record = {"owner": owner, "name": "hc-whisper-reference-" + owner, "phase": "creating",
                  "source_revision": revision, "image_id": image_id, "model_pin": model,
                  "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                  "helper_sha256": self.source_digest(), "memory_before": before,
                  "trial_deadline_monotonic": time.monotonic() + 3600,
                  "loading_deadline_monotonic": time.monotonic() + 300}
        write_json(self.state / "active.json", record)
        try:
            record["container_id"] = command(self.run_arguments(record))
            record["phase"] = "awaiting_guard"
            write_json(self.state / "active.json", record, replace=True)
            self.owned_container(record)
            return record
        except BaseException:
            try:
                record["cleanup"] = self.guard.terminate_owned(record, loading=True)
            except Exception:  # noqa: BLE001 - Preserve a failed startup without leaking exception values.
                record["cleanup"] = {"status": "needs_operator_inspection", "force_kill": False}
            write_json(self.state / ("failed-start-" + owner + ".json"), record)
            if record["cleanup"]["status"] == "stopped":
                self.guard.remove_active(record)
            raise

    def release_load(self, record):
        if record["phase"] != "awaiting_guard":
            raise Refused("Load was already released")
        if self.guard.alive(record).get("qualification_failed") is not False:
            raise Refused("Observed resource failure invalidates this trial")
        self.guard.sample(record, loading=True)
        self.owned_container(record)
        program = "import os;f=os.open('/tmp/load-enabled',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(f)"
        command(["docker", "exec", record["container_id"], "python3", "-I", "-B", "-c", program])
        record["phase"] = "loading"
        write_json(self.state / "active.json", record, replace=True)
        return {"status": "load_released_with_live_guard", "container_id": record["container_id"]}

    def wait_ready(self):
        record = read_json(self.state / "active.json")
        if record["phase"] != "loading":
            raise Refused("Release the guarded load first")
        while time.monotonic() < record["loading_deadline_monotonic"]:
            if self.guard.alive(record).get("qualification_failed") is not False:
                raise Refused("Observed resource failure invalidates this trial")
            sample = self.guard.sample(record)
            actual = self.owned_container(record)
            if not actual["State"]["Running"]:
                write_json(self.state / ("failed-load-" + record["owner"] + ".json"), self.resource(record))
                raise Refused("Reference exited during guarded load; no automatic retry")
            try:
                value = self.health(record)
            except Refused:
                time.sleep(.25)
                continue
            if value["inference_busy"]:
                raise Refused("Unexpected inference during load")
            with self.guard.lock():
                current = read_json(self.state / "active.json")
                if not same_trial(record, current):
                    raise Refused("Candidate changed during load")
                current.update(phase="ready", health=value, memory_loaded=sample, monitor=self.guard.alive(record))
                write_json(self.state / "active.json", current, replace=True)
                write_json(self.state / ("ready-" + record["owner"] + ".json"), current)
                return current
        raise Refused("Guarded startup deadline exceeded")

    def status(self, *, guard_only=False):
        record = read_json(self.state / "active.json")
        self.owned_container(record)
        monitor = self.guard.alive(record)
        if guard_only:
            result = monitor
        else:
            result = self.resource(record)
            result["monitor"] = monitor
            if result["running"]:
                result["health"] = self.health(record, require_ready=False)
        if not same_trial(record, read_json(self.state / "active.json")):
            raise Refused("Candidate changed during read-only observation")
        return result

    def stop(self, record):
        loading = record["phase"] in ("creating", "awaiting_guard", "loading") and not record.get("request_id")
        result = self.guard.terminate_owned(record, loading=loading)
        self.guard.remove_active(record)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "start", "guard", "release-load", "wait-ready", "status", "guard-status", "request-start", "request-end", "stop", "emergency-abort"))
    parser.add_argument("--revision")
    parser.add_argument("--image-id")
    parser.add_argument("--request-id")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise Refused("Run as the ordinary Docker-authorized user")
    private_directory(STATE)
    runtime = Runtime()
    if args.action == "guard":
        result = runtime.guard.run()
    elif args.action == "wait-ready":
        result = runtime.wait_ready()
    elif args.action in ("status", "guard-status"):
        result = runtime.status(guard_only=args.action == "guard-status")
        write_json(STATE / (args.action + "-" + secrets.token_hex(8) + ".json"), result)
    else:
        with runtime.guard.lock():
            if args.action == "build":
                result = runtime.build(args.revision)
            elif args.action == "start":
                result = runtime.start(args.revision, args.image_id)
            else:
                record = read_json(STATE / "active.json")
                runtime.owned_container(record)
                if args.action == "release-load":
                    result = runtime.release_load(record)
                elif args.action == "request-start":
                    result = runtime.guard.request_mark(record)
                elif args.action == "request-end":
                    if not args.request_id:
                        raise Refused("Exact request marker required")
                    result = runtime.guard.request_mark(record, finish_id=args.request_id)
                elif args.action == "emergency-abort":
                    result = runtime.guard.emergency_abort(record)
                else:
                    result = runtime.stop(record)
            write_json(STATE / (args.action + "-" + secrets.token_hex(8) + ".json"), result)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - Emit only the failure category, never private values.
        print(json.dumps({"status": "refused", "failure_class": type(error).__name__}))
        raise SystemExit(1) from None
