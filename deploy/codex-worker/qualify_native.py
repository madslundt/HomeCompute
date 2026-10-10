"""Credential-free native sandbox check for the disabled dedicated-VM candidate.

Runs as the unit's trusted controller, using only synthetic files and its
reserved job UID. This checks sandbox/DAC/process boundaries, not production
network policy, model authentication, publisher authority or reboot acceptance.
"""
from __future__ import annotations

import json
import os
import pwd
import subprocess
import tempfile
import time
import tomllib
import uuid
from pathlib import Path

from worker import FILE_SIZE_BUDGETS, MAX_EVIDENCE_BYTES, FileBudget, Worker


class SyntheticWorker(Worker):
    def running(self, task_id: str) -> bool:
        return True


def budget_probe(worker: Worker, job: Path, evidence: Path) -> None:
    for kind, maximum in FILE_SIZE_BUDGETS.items():
        program = "\n".join([
            "import errno, pathlib, resource",
            f"assert resource.getrlimit(resource.RLIMIT_FSIZE) == ({maximum}, {maximum})",
            "with pathlib.Path('budget-sparse').open('wb') as output:",
            "    output.truncate(268435457)" if kind == "tests" else "    output.truncate(10485760)",
            f"    try: output.truncate({maximum + 1})",
            "    except OSError as error: assert error.errno == errno.EFBIG",
            "    else: raise AssertionError('file-size ceiling not enforced')",
            "pathlib.Path('budget-sparse').unlink()",
        ])
        if worker.run(["python3", "-c", program], job, evidence / f"budget-{kind}.log",
                      "synthetic", 10, file_budget=kind):
            raise RuntimeError("native file-size budget failed")
    output = evidence / "budget-output.log"
    try:
        worker.run(["python3", "-c", "import os; os.write(1, b'x' * 11534336)"],
                   job, output, "synthetic", 10, file_budget="tests")
    except ValueError as error:
        assert str(error) == "subprocess evidence exceeds budget"
    else:
        raise RuntimeError("oversized evidence accepted")
    assert output.stat().st_size == MAX_EVIDENCE_BYTES


def checkout_probe(parent: Path) -> None:
    """Real execute/checkout path with generated local Git and no model backend."""
    fixture = parent / "checkout-fixture"
    fixture.mkdir(mode=0o700)
    (fixture / "src").mkdir()
    (fixture / "src/example.py").write_text("value = 1\n")
    (fixture / ".codex").mkdir()
    (fixture / ".codex/data-policy.json").write_text('{"schema_version":1,"classification":"cloud_allowed"}')
    Worker.git(fixture, "init", "-q", "-b", "main")
    Worker.git(fixture, "config", "user.name", "Synthetic qualification")
    Worker.git(fixture, "config", "user.email", "synthetic@example.invalid")
    Worker.git(fixture, "add", "src", ".codex")
    Worker.git(fixture, "commit", "-q", "-m", "Synthetic checkout qualification")
    base = Worker.git(fixture, "rev-parse", "HEAD").decode().strip()
    probe = "\n".join([
        "import os, pathlib, subprocess",
        "assert 'CODEX_API_KEY' not in os.environ",
        "os.environ['GIT_OPTIONAL_LOCKS'] = '0'",
        "assert subprocess.check_output(['git','rev-parse','--is-inside-work-tree']).strip() == b'true'",
        "subprocess.run(['git','status','--porcelain'], check=True)",
        "subprocess.run(['git','diff','--stat'], check=True)",
        "common = pathlib.Path(subprocess.check_output(['git','rev-parse','--git-common-dir'],text=True).strip())",
        "index = pathlib.Path(subprocess.check_output(['git','rev-parse','--git-path','index'],text=True).strip())",
        "for path in (common/'config', index):",
        "    assert path.stat().st_uid != os.getuid() and path.stat().st_gid == os.getgid()",
        "    assert len(path.read_bytes()) > 0",
        "    try: path.write_bytes(b'unexpected metadata write')",
        "    except OSError: pass",
        "    else: raise AssertionError('job could write trusted Git metadata')",
        "    try: path.chmod(0o700)",
        "    except OSError: pass",
        "    else: raise AssertionError('job could chmod trusted Git metadata')",
    ])
    project = {"repository": "example/synthetic", "base_sha": base, "base_branch": "main",
               "classification": "cloud_allowed", "tests": ["python3", "-c", probe], "write_prefixes": ["src/"]}

    class FixtureWorker(SyntheticWorker):
        def clone(self, repository: str, target: Path, evidence: Path) -> None:
            # Local fixture substitutes the Git transport only. Actual trusted
            # checkout permissions and execute ordering remain under test.
            assert repository == "example/synthetic"
            self.git(fixture, "clone", "--no-checkout", str(fixture), str(target))
            evidence.write_text("Local synthetic clone; no authentication.\n")

        def run(self, argv: list[str], directory: Path, evidence: Path, task_id: str,
                seconds: int, prompt: str | None = None, codex: bool = False,
                file_budget: FileBudget = "default") -> int:
            if codex:
                overrides = [argv[index + 1] for index, argument in enumerate(argv) if argument == "-c"]
                shell = tomllib.loads("\n".join(overrides))["shell_environment_policy"]
                assert shell == {"exclude": ["CODEX_API_KEY", "OPENAI_API_KEY"],
                                 "set": {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory",
                                         "GIT_CONFIG_VALUE_0": str(directory)}}
                # Parse the exact production argv without loading credentials
                # or contacting a model. Unknown pinned-CLI flags fail here.
                if super().run([*argv, "--help"], directory, evidence,
                               task_id, 10, codex=False):
                    raise RuntimeError("production Codex exec argv rejected by pinned CLI")
                # Explicit fixed-result substitute; never invoke exec/auth/model.
                (directory / "src/example.py").write_text("value = 2\n")
                evidence.write_text('{"type":"thread.started","thread_id":"synthetic-checkout"}\n')
                return 0
            if argv == project["tests"]:
                argv = ["codex", "-a", "never", "--sandbox", "workspace-write", "-c",
                        'sandbox_mode="workspace-write"', "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                        "sandbox", "--", *argv]
            return super().run(argv, directory, evidence, task_id, seconds, prompt, codex=False,
                               file_budget=file_budget)

    work = parent / "checkout-jobs"
    work.mkdir(mode=0o711)
    work.chmod(0o711)
    worker = FixtureWorker(work, "synthetic-unused", {"synthetic": project})
    task_id = str(uuid.uuid4())
    try:
        result = worker.execute({"id": task_id, "project": "synthetic", "policy": project, "body": {}})
    except Exception:
        # Only generated fixture evidence; no authentication is provisioned.
        for name in ("baseline.log", "tests.log", "codex.jsonl"):
            path = work / "evidence" / task_id / name
            if path.exists():
                print(json.dumps({"synthetic_diagnostic": name, "log": path.read_text()[-8192:]}), flush=True)
        raise
    if not result["ok"] or result["tests_passed"] is not True or len(result["files"]) != 1:
        raise RuntimeError("synthetic checkout/model-substitute regression failed")


def main() -> None:
    os.umask(0o077)
    root = Path(os.environ.get("CODEX_WORKER_ROOT", "/srv/codex-work"))
    if pwd.getpwuid(os.getuid()).pw_name != "codex-controller":
        raise RuntimeError("requires dedicated codex-controller service identity")
    if (not root.is_dir() or root.is_symlink() or not root.is_mount()
            or root.stat().st_dev == root.parent.stat().st_dev):
        raise RuntimeError("requires dedicated mounted work filesystem")
    filesystem = os.statvfs(root)
    if not 4 * 1024 ** 3 <= filesystem.f_blocks * filesystem.f_frsize <= 12 * 1024 ** 3:
        raise RuntimeError("requires a dedicated 4..12 GiB work filesystem")
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o777 != 0o711:
        raise RuntimeError("work filesystem must be controller-owned mode 0711")
    if subprocess.check_output(["codex", "--version"], timeout=10).strip() != b"codex-cli 0.145.0":
        raise RuntimeError("unexpected Codex version")
    if not Path("/usr/bin/bwrap").is_file():
        raise RuntimeError("distro bubblewrap required; global AppArmor exceptions prohibited")
    uid = 20000
    if any(10000 <= account.pw_uid < 60000 for account in pwd.getpwall()):
        raise RuntimeError("reserved job UID range contains an account")
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                line = next(line for line in (entry / "status").read_text().splitlines() if line.startswith("Uid:"))
            except (FileNotFoundError, PermissionError, StopIteration):
                continue
            if any(10000 <= int(value) < 60000 for value in line.split()[1:]):
                raise RuntimeError("reserved job UID range already has processes")
    with tempfile.TemporaryDirectory(dir=root, prefix="qualification-") as temporary:
        parent = Path(temporary)
        parent.chmod(0o711)
        evidence = parent / "evidence"
        evidence.mkdir(mode=0o700)
        job = parent / "job"
        job.mkdir(mode=0o700)
        os.chown(job, uid, uid)
        worker = SyntheticWorker(parent, "synthetic-unused", {})
        worker.job_root = job
        worker.deadline = time.monotonic() + 45
        worker.sandbox_preflight(evidence, "synthetic")
        # Confirm the installed CLI actually selects the distro binary covered
        # by Ubuntu's scoped AppArmor profile. Merely finding bwrap on PATH is
        # not proof that the CLI used it. This second probe remains synthetic.
        trace = job / "bwrap-exec.log"
        argv = ["strace", "-f", "-qq", "-e", "trace=execve", "-o", str(trace), "codex", "-a", "never",
                "--sandbox", "workspace-write", "-c", 'sandbox_mode="workspace-write"',
                "-c", "sandbox_workspace_write.exclude_slash_tmp=true", "sandbox", "--",
                "python3", "-c", "\n".join([
                    "import ctypes, json",
                    "from pathlib import Path",
                    "label = Path('/proc/self/attr/current').read_text().strip()",
                    "assert 'unpriv_bwrap' in label and 'enforce' in label",
                    "libc = ctypes.CDLL(None, use_errno=True)",
                    "assert libc.unshare(0x10000000 | 0x00020000) != 0, 'sandbox child created privileged mount namespace'",
                    "print(json.dumps({'apparmor_child_profile': label, 'nested_privileged_namespace': 'denied'}))",
                ])]
        if worker.run(argv, job, evidence / "bwrap-selection.log", "synthetic", 15):
            raise RuntimeError("distro bwrap selection probe failed")
        executions = trace.read_text()
        if 'execve("/usr/bin/bwrap",' not in executions:
            raise RuntimeError("Codex did not execute the scoped distro bubblewrap binary")
        selected = [json.loads(line) for line in (evidence / "bwrap-selection.log").read_text().splitlines()
                    if line.startswith('{"apparmor_child_profile":')]
        if not selected:
            raise RuntimeError("scoped child AppArmor receipt missing")
        program = "\n".join([
            "import os, pathlib, subprocess",
            "assert os.getuid() == 20000 and os.getgroups() == []",
            "assert not any(name in os.environ for name in ('CODEX_API_KEY', 'CREDENTIALS_DIRECTORY'))",
            "lines = pathlib.Path('/proc/self/status').read_text().splitlines()",
            "for field in ('CapInh:', 'CapPrm:', 'CapEff:', 'CapAmb:'):",
            "    assert int(next(line for line in lines if line.startswith(field)).split()[1], 16) == 0",
            "try: os.kill(os.getppid(), 0)",
            "except PermissionError: pass",
            "else: raise AssertionError('controller signal permitted')",
            "child = subprocess.Popen(['python3', '-c', 'import ctypes,time; ctypes.CDLL(None).prctl(4,0,0,0,0); time.sleep(60)'], start_new_session=True)",
            "pathlib.Path('detached-pid').write_text(str(child.pid))",
        ])
        if worker.run(["python3", "-c", program], job, evidence / "identity.log", "synthetic", 10):
            raise RuntimeError("job identity/capability boundary failed")
        child_pid = int((job / "detached-pid").read_text())
        try:
            status = Path(f"/proc/{child_pid}/status").read_text()
            if not any(line.startswith("State:") and "Z" in line for line in status.splitlines()):
                raise RuntimeError("detached non-dumpable job child survived cleanup")
        except FileNotFoundError:
            pass
        budget_probe(worker, job, evidence)
        checkout_probe(parent)
    print(json.dumps({"event": "native_worker_synthetic_qualification", "sandbox": "workspace-write",
                      "job_uid": uid, "capabilities": "cleared", "credentials_accessed": False,
                      "codex_bwrap_executable": "/usr/bin/bwrap", "bwrap_selection": "observed_execve",
                      "apparmor_child_profile": selected[-1]["apparmor_child_profile"],
                      "nested_privileged_namespace": "denied",
                      "detached_nondumpable_cleanup": "pass",
                      "trusted_checkout_and_job_git_reads": "pass",
                      "trusted_git_metadata_write_chmod": "denied",
                      "production_exec_argv_help": "pass",
                      "shell_environment_config": "exact_git_trust_and_api_key_exclusions",
                      "file_size_budget_bytes": FILE_SIZE_BUDGETS,
                      "stored_evidence_budget_bytes": MAX_EVIDENCE_BYTES,
                      "file_size_and_oversized_evidence_probes": "pass",
                      "production_network_qualified": False, "authenticated_codex_execution": False}))


if __name__ == "__main__":
    main()
