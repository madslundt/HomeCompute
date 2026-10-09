"""Synthetic Linux/container isolation check. No credentials, model or API calls.

Run in the worker image with its reviewed capabilities, read-only root,
network=none, and temporary /work, /tmp and /run/secrets mounts. The container
must be discarded afterward. This checks DAC/process cleanup, not Codex auth.
"""
from __future__ import annotations

import json
import base64
import http.client
import os
from pathlib import Path
import sys
import tempfile
import time
import subprocess

sys.path.insert(0, "/opt/worker")
from worker import Worker


class SyntheticWorker(Worker):
    def running(self, task_id: str) -> bool:
        return True


def proxy_check() -> None:
    if os.getuid() != 1000:
        raise RuntimeError("requires disposable unprivileged proxy container")
    config = Path("/tmp/squid-test.conf")
    config.write_bytes(base64.b64decode(os.environ["HOMECOMPUTE_TEST_SQUID_CONFIG"], validate=True))
    process = subprocess.Popen(["squid", "-N", "-f", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", 3128, timeout=2)
                connection.request("GET", "http://1.1.1.1/")
                assert connection.getresponse().status == 403
                connection.close()
                break
            except OSError:
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode())
                time.sleep(0.1)
        else:
            raise TimeoutError("proxy startup did not complete")
        for target in ("127.0.0.1:443", "1.1.1.1:443", "api.github.com:8443"):
            connection = http.client.HTTPConnection("127.0.0.1", 3128, timeout=2)
            connection.request("CONNECT", target)
            assert connection.getresponse().status == 403
            connection.close()
        print(json.dumps({"unprivileged_proxy_startup": "pass", "private_unknown_host_and_non443_denials": "pass"}))
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


def main() -> None:
    if "--proxy" in sys.argv:
        proxy_check()
        return
    if sys.platform != "linux" or os.getuid() != 0:
        raise RuntimeError("requires disposable Linux worker container controller")
    os.umask(0o077)
    secret = Path("/run/secrets/operator-test")
    secret.write_text("synthetic sentinel only")
    secret.chmod(0o400)
    with tempfile.TemporaryDirectory(dir="/work") as directory:
        root = Path(directory)
        root.chmod(0o711)
        evidence = root / "evidence"
        evidence.mkdir(mode=0o700)
        (evidence / "private").write_text("synthetic evidence")
        job = root / "job"
        job.mkdir(mode=0o700)
        os.chown(job, 20000, 20000)
        worker = SyntheticWorker(root, "synthetic-unused", {})
        worker.job_root = job
        worker.deadline = time.monotonic() + 20
        program = r'''
import ctypes,json,os,pathlib,subprocess,tempfile,time
assert os.getuid()==20000 and os.getgroups()==[]
assert 'CODEX_API_KEY' not in os.environ
for path in ['/run/secrets/operator-test',os.environ['EVIDENCE_TEST']]:
 try: pathlib.Path(path).read_text()
 except PermissionError: pass
 else: raise AssertionError('protected read succeeded')
try: os.kill(os.getppid(),0)
except PermissionError: pass
else: raise AssertionError('job could signal controller')
with tempfile.TemporaryFile() as f: f.write(b'synthetic temporary data')
assert pathlib.Path(tempfile.gettempdir()).parent.name=='job'
child=subprocess.Popen(['python3','-c',
 'import ctypes,time; ctypes.CDLL(None).prctl(4,0,0,0,0); time.sleep(60)'],start_new_session=True)
pathlib.Path('child-pid').write_text(str(child.pid))
print(json.dumps({'uid':os.getuid(),'secret_read_denied':True,'controller_signal_denied':True,'tempfile':True}),flush=True)
'''
        # run() deliberately forwards only fixed proxy/PATH environment names.
        # Supply the known protected evidence path as data in this synthetic code.
        program = program.replace("os.environ['EVIDENCE_TEST']", repr(str(evidence / "private")))
        code = worker.run(["python3", "-c", program], job, evidence / "check.log", "synthetic", 15)
        if code:
            raise AssertionError((evidence / "check.log").read_text())
        pid = int((job / "child-pid").read_text())
        try:
            status = Path(f"/proc/{pid}/status").read_text()
            if not any(line.startswith("State:") and "Z" in line for line in status.splitlines()):
                raise AssertionError("detached non-dumpable job child survived cleanup")
        except FileNotFoundError:
            pass
        worker.deadline = time.monotonic() + 40
        try:
            worker.sandbox_preflight(evidence, "synthetic")
            sandbox = "pass"
        except RuntimeError:
            if "bwrap" not in (evidence / "sandbox-preflight.log").read_text():
                raise
            sandbox = "blocked_by_namespace_policy_before_authentication"
        print(json.dumps({"linux_uid_secret_temp_and_process_cleanup": "pass",
                          "native_workspace_write_preflight": sandbox,
                          "paid_api_calls": 0, "production_changes": False}))


if __name__ == "__main__":
    main()
