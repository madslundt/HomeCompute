"""Private manifest validation and one-command guarded CUDA qualification."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import shutil
import stat
import time
import wave


import importlib.util

_manifest_spec = importlib.util.spec_from_file_location(
    "_whisper_cuda_qualification_manifest", Path(__file__).with_name("whisper-cuda-qualification-manifest.py"))
_manifest = importlib.util.module_from_spec(_manifest_spec)
_manifest_spec.loader.exec_module(_manifest)
_validate_manifest = _manifest._validate_manifest
MAX_INPUTS = _manifest.MAX_INPUTS
MAX_AUDIO_SECONDS = _manifest.MAX_AUDIO_SECONDS
MAX_UPLOAD_BYTES = _manifest.MAX_UPLOAD_BYTES
MAX_NATIVE_TIMEOUT = _manifest.MAX_NATIVE_TIMEOUT
MAX_MANIFEST_BYTES = _manifest.MAX_MANIFEST_BYTES

MAX_RESPONSE_BYTES = 8_000_000
PROVENANCE_HEADERS = {
    "x-homecompute-stt-model-sha256": "model_sha256",
    "x-homecompute-stt-model-snapshot": "model_snapshot",
    "x-homecompute-stt-runtime": "runtime",
    "x-homecompute-stt-runtime-commit": "runtime_commit",
    "x-homecompute-stt-homecompute-revision": "homecompute_revision",
    "x-homecompute-stt-image-id": "image_id",
}
REQUEST_FIELDS = {
    "model": "whisper", "response_format": "verbose_json", "language": "auto",
    "temperature": "0", "temperature_inc": "0.2", "max_context": "0",
    "beam_size": "5", "best_of": "5", "max_len": "-1",
    "carry_initial_prompt": "false", "translate": "false", "no_timestamps": "false",
}


class QualificationError(ValueError):
    def __init__(self, stage: str, failure_class: str):
        self.stage = stage
        self.failure_class = failure_class
        super().__init__('Qualification refused at ' + stage)


def _write_private(path: Path, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, os.geteuid(), os.getegid())
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, value):
    _write_private(path, json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False).encode("utf-8"))


def _progress(qualification_id, stage, input_index=None):
    event = {"qualification_id": qualification_id, "stage": stage}
    if input_index is not None:
        event["input_index"] = input_index
    print(json.dumps(event, separators=(",", ":")), flush=True)


def _stage_audio(state_dir: Path, inputs):
    staged = []
    for item in inputs:
        name = "input-" + item["id"] + ".wav"
        path = state_dir / name
        _write_private(path, item["audio"])
        staged.append({**{key: item[key] for key in ("id", "bytes", "sha256", "frames")},
                       "path": path, "file": name, "audio": item["audio"]})
    return staged


def _client_program(max_bytes: int, timeout: int):
    fields = json.dumps(REQUEST_FIELDS, separators=(",", ":"))
    return '''import base64,json,sys,urllib.request,urllib.error\n'''+f'''MAX_BYTES={max_bytes}\nMAX_RESPONSE={MAX_RESPONSE_BYTES}\nTIMEOUT={timeout}\nFIELDS={fields}\nHEADER_NAMES={list(PROVENANCE_HEADERS.keys())!r}\ndef pack(ok,status,headers,body,truncated=False):\n print(json.dumps({{"ok":ok,"status":status,"headers":{{name:headers.get(name) for name in HEADER_NAMES}},"body_base64":base64.b64encode(body).decode("ascii"),"body_truncated":truncated}},separators=(",",":")))\naudio=sys.stdin.buffer.read(MAX_BYTES+1)\nif len(audio)>MAX_BYTES: raise SystemExit(30)\nboundary="hc-qual-"+__import__("secrets").token_hex(12)\nparts=[]\nfor name,value in FIELDS.items():\n parts.extend([f"--{{boundary}}\\r\\nContent-Disposition: form-data; name=\\\"{{name}}\\\"\\r\\n\\r\\n".encode(),value.encode(),b"\\r\\n"])\nparts.extend([f"--{{boundary}}\\r\\nContent-Disposition: form-data; name=\\\"file\\\"; filename=\\\"audio.wav\\\"\\r\\nContent-Type: audio/wav\\r\\n\\r\\n".encode(),audio,f"\\r\\n--{{boundary}}--\\r\\n".encode()])\nbody=b"".join(parts)\nkey=open("/run/secrets/whisper_cpp_api_key","rb").read().strip()\nreq=urllib.request.Request("http://127.0.0.1:8000/v1/audio/transcriptions",body,headers={{"Authorization":"Bearer "+key.decode("ascii"),"Content-Type":"multipart/form-data; boundary="+boundary}})\ntry:\n response=urllib.request.urlopen(req,timeout=TIMEOUT)\nexcept urllib.error.HTTPError as error:\n try:\n  payload=error.read(MAX_RESPONSE+1)\n  pack(False,error.code,error.headers or {{}},payload[:MAX_RESPONSE],len(payload)>MAX_RESPONSE)\n finally:\n  error.close()\n raise SystemExit(0)\nexcept (TimeoutError,urllib.error.URLError,OSError) as error:\n print(json.dumps({{"ok":False,"failure_class":type(error).__name__}},file=sys.stderr));raise SystemExit(31)\nwith response:\n payload=response.read(MAX_RESPONSE+1)\n pack(response.status<300,response.status,response.headers,payload[:MAX_RESPONSE],len(payload)>MAX_RESPONSE)\n'''


def _decode_response(raw_stdout: str):
    try:
        envelope = json.loads(raw_stdout)
        if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
            raise ValueError
        body = base64.b64decode(envelope["body_base64"], validate=True)
        headers = envelope["headers"]
        status = envelope["status"]
        truncated = envelope.get("body_truncated")
        if (not isinstance(headers, dict) or set(headers) != set(PROVENANCE_HEADERS) or
                any(value is not None and not isinstance(value, str) for value in headers.values()) or
                not isinstance(status, int) or isinstance(status, bool) or
                type(truncated) is not bool or len(body) > MAX_RESPONSE_BYTES):
            raise ValueError
        return envelope["ok"], status, headers, body, truncated
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("candidate returned an invalid bounded response envelope") from None


def _persist_result(state_dir: Path, qualification_id: str, item, status, headers, body,
                    inference_elapsed_seconds: float, *, ok: bool, body_truncated: bool):
    name = f"result-{qualification_id}-{item['id']}.json"
    record = {
        "schema_version": 1, "qualification_id": qualification_id,
        "input": {key: item[key] for key in ("id", "bytes", "sha256", "frames")},
        "request": REQUEST_FIELDS, "inference_elapsed_seconds": inference_elapsed_seconds,
        "ok": ok, "http_status": status, "headers": headers,
        "body_truncated": body_truncated,
        "response_body_base64": base64.b64encode(body).decode("ascii"),
    }
    path = state_dir / name
    _write_json(path, record)
    return name, record


def _validate_result(ok, status, headers, body, revision, image_id, model_sha, model_snapshot, runtime_commit,
                     *, body_truncated=False):
    if not ok or body_truncated or status != 200:
        raise ValueError("candidate returned a non-success HTTP status")
    expected = {
        "x-homecompute-stt-model-sha256": model_sha,
        "x-homecompute-stt-model-snapshot": model_snapshot,
        "x-homecompute-stt-runtime": "whisper.cpp",
        "x-homecompute-stt-runtime-commit": runtime_commit,
        "x-homecompute-stt-homecompute-revision": revision,
        "x-homecompute-stt-image-id": image_id,
    }
    if any(headers.get(name) != value for name, value in expected.items()):
        raise ValueError("candidate response provenance differs from the prepared pins")
    try:
        decoded = json.loads(body)
        if not isinstance(decoded, dict) or not isinstance(decoded.get("text"), str) or not isinstance(decoded.get("segments"), list):
            raise ValueError
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise ValueError("candidate returned invalid verbose JSON") from None


def _export(operator_dir_fd: int, owner: int, name: str, value: bytes):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                 0o600, dir_fd=operator_dir_fd)
    try:
        os.fchown(fd, owner, -1)
        os.fchmod(fd, 0o600)
        view = memoryview(value)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _candidate_transaction(lifecycle, revision):
    journal = lifecycle.STATE / "cuda-candidate.json"
    try:
        info = journal.lstat()
    except OSError:
        return None
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or
            info.st_uid != os.geteuid()):
        raise ValueError("CUDA candidate journal is not protected")
    value = json.loads(journal.read_text())
    if value.get("revision") != revision:
        raise ValueError("CUDA candidate journal belongs to another release")
    return value


@contextlib.contextmanager
def _protect_rollback_from_signals():
    watched = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {item: signal.getsignal(item) for item in watched}
    try:
        for item in watched:
            signal.signal(item, signal.SIG_IGN)
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


def _safe_failure(error):
    metadata = getattr(error, "metadata", None)
    if isinstance(metadata, dict):
        result = {}
        aliases = {"failure_id": "diagnostic_id", "failure_stage": "stage",
                   "failure_class": "failure_class", "failure_executable": "executable",
                   "failure_exit_code": "exit_code", "failure_classification": "classification"}
        for key, source in aliases.items():
            value = metadata.get(source)
            if value is None:
                value = metadata.get(key)
            if value is None and source == "failure_class":
                value = metadata.get("failure_class")
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
                result[key] = value
            elif isinstance(value, int) and not isinstance(value, bool) and -255 <= value <= 255:
                result[key] = value
        return result
    name = type(error).__name__ if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", type(error).__name__) else "Error"
    result = {"failure_class": name}
    if getattr(error, "executable", None) in {"docker", "python3", "git", "iptables"}:
        result["executable"] = error.executable
    code = getattr(error, "exit_code", None)
    if isinstance(code, int) and not isinstance(code, bool) and -255 <= code <= 255:
        result["exit_code"] = code
    classification = getattr(error, "classification", None)
    if classification in {"cuda-out-of-memory", "vllm-memory-budget-refusal", "model-cache-integrity",
                          "container-health", "permission-denied", "host-memory-headroom",
                          "relay-smoke", "unknown"}:
        result["classification"] = classification
    return result


def qualify(lifecycle, manifest_path: str | Path, revision: str, *, operator_uid: int):
    manifest = Path(manifest_path)
    try:
        document = _validate_manifest(manifest, operator_uid)
    except BaseException as error:
        name = type(error).__name__ if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", type(error).__name__) else "Error"
        raise QualificationError("manifest-validation", name) from None
    qualification_id = os.urandom(8).hex()
    try:
        lifecycle.ensure_no_cuda_candidate_transaction("cuda-candidate-qualify")
        if lifecycle.inspect(lifecycle.CUDA_CANDIDATE):
            raise ValueError("A CUDA candidate already exists; inspect its owning transaction")
    except Exception as error:
        os.close(document["operator_dir_fd"])
        name = type(error).__name__ if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", type(error).__name__) else "Error"
        raise QualificationError("candidate-preflight", name) from None
    state_root = lifecycle.STATE / "cuda-qualifications"
    state_dir = state_root / qualification_id
    try:
        state_root.mkdir(mode=0o700, parents=False, exist_ok=True)
        if (state_root.is_symlink() or state_root.stat().st_uid != os.geteuid() or
                stat.S_IMODE(state_root.stat().st_mode) != 0o700):
            raise ValueError("CUDA qualification state directory is not protected")
        state_dir.mkdir(mode=0o700)
        staged = _stage_audio(state_dir, document["inputs"])
    except Exception as error:
        if state_dir.exists() and not state_dir.is_symlink():
            shutil.rmtree(state_dir)
        os.close(document["operator_dir_fd"])
        name = type(error).__name__ if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", type(error).__name__) else "Error"
        raise QualificationError("artifact-staging", name) from None
    receipt_name = "receipt-" + qualification_id + ".json"
    receipt_path = state_dir / receipt_name
    receipt = {"schema_version": 1, "qualification_id": qualification_id,
               "state": "staged", "stage": "artifact-staging", "revision": revision,
               "inputs": [{key: item[key] for key in ("id", "bytes", "sha256", "frames")} for item in staged],
               "failure": None, "rollback_failure": None, "timings": {},
               "rollback": "not-started", "results": []}
    _write_json(receipt_path, receipt)
    started = False
    failure = None
    exports = []
    action_started = time.monotonic()
    original_signal_handlers = {}
    def interrupt_qualification(_signum, _frame):
        raise InterruptedError("qualification interrupted")
    for watched in (signal.SIGTERM, signal.SIGHUP):
        original_signal_handlers[watched] = signal.getsignal(watched)
        signal.signal(watched, interrupt_qualification)
    try:
        receipt.update(state="running", stage="prepare-candidate")
        _write_json(receipt_path, receipt) if not receipt_path.exists() else _replace_private_json(receipt_path, receipt)
        _progress(qualification_id, "prepare")
        phase_started = time.monotonic()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                lifecycle.prepare_cuda_candidate(revision)
        finally:
            receipt["timings"]["prepare_seconds"] = round(time.monotonic() - phase_started, 3)
        receipt.update(stage="start-candidate")
        _replace_private_json(receipt_path, receipt)
        _progress(qualification_id, "start")
        started = True
        phase_started = time.monotonic()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                lifecycle.cuda_candidate_start(
                    revision, max_audio_seconds=document["max_audio_seconds"],
                    max_upload_bytes=document["max_upload_bytes"],
                    native_timeout_seconds=document["native_timeout_seconds"])
        finally:
            receipt["timings"]["start_seconds"] = round(time.monotonic() - phase_started, 3)
            _replace_private_json(receipt_path, receipt)
        tx = _candidate_transaction(lifecycle, revision)
        if not tx or tx.get("state") != "candidate-running" or tx.get("candidate_exposed") is not True:
            raise ValueError("guarded CUDA candidate did not reach qualified running state")
        image_id = tx.get("candidate_image_id")
        for input_index, item in enumerate(staged, 1):
            receipt.update(stage="inference-" + item["id"])
            _replace_private_json(receipt_path, receipt)
            _progress(qualification_id, "inference", input_index)
            command = ["docker", "exec", "-i", lifecycle.CUDA_CANDIDATE, "python3", "-c",
                       _client_program(document["max_upload_bytes"], document["native_timeout_seconds"])]
            inference_started = time.monotonic()
            result = lifecycle.run_with_input(command, item["audio"],
                                              timeout=document["native_timeout_seconds"] + 180)
            ok, status, headers, body, body_truncated = _decode_response(result.stdout)
            inference_elapsed = round(time.monotonic() - inference_started, 3)
            name, record = _persist_result(state_dir, qualification_id, item, status, headers, body,
                                           inference_elapsed, ok=ok, body_truncated=body_truncated)
            receipt["timings"].setdefault("per_input_seconds", {})[item["id"]] = inference_elapsed
            receipt["results"].append(name)
            _replace_private_json(receipt_path, receipt)
            _validate_result(ok, status, headers, body, revision, image_id,
                             lifecycle.CPP_MODEL_SHA256, lifecycle.CPP_MODEL_SNAPSHOT,
                             lifecycle.CPP_RUNTIME_COMMIT, body_truncated=body_truncated)
        receipt.update(state="completed", stage="rollback", rollback="pending")
    except BaseException as error:
        failure = error
        receipt.update(state="failed", stage=getattr(error, "stage", receipt.get("stage")),
                       failure=_safe_failure(error), rollback="pending" if started else "not-required")
    finally:
        if started:
            _progress(qualification_id, "rollback")
            try:
                tx = _candidate_transaction(lifecycle, revision)
                if tx and tx.get("state") != "rolled-back":
                    idle_timeout = document["native_timeout_seconds"] + 180
                    rollback_started = time.monotonic()
                    try:
                        with contextlib.redirect_stdout(io.StringIO()), _protect_rollback_from_signals():
                            lifecycle.cuda_candidate_stop(revision, idle_timeout=idle_timeout)
                    except BaseException as error:
                        receipt["timings"]["rollback_seconds"] = round(time.monotonic() - rollback_started, 3)
                        current = _candidate_transaction(lifecycle, revision)
                        if current and current.get("state") != "rolled-back":
                            lifecycle.cuda_candidate_failure(current, error, rollback=True)
                        receipt.update(state="rollback-failed", stage="rollback",
                                       failure=receipt.get("failure"),
                                       rollback_failure=_safe_failure(error), rollback="failed")
                    else:
                        receipt["timings"]["rollback_seconds"] = round(time.monotonic() - rollback_started, 3)
                        receipt["rollback"] = "completed"
                else:
                    receipt["rollback"] = "completed" if tx else "not-required"
            except BaseException as error:
                receipt.update(state="rollback-failed", stage="rollback",
                               failure=receipt.get("failure"),
                               rollback_failure=_safe_failure(error), rollback="failed")
        if failure is None and receipt.get("state") != "rollback-failed":
            receipt.update(state="completed", stage="complete")
        receipt["timings"]["total_seconds"] = round(time.monotonic() - action_started, 3)
        receipt["finished_unix"] = int(time.time())
        _replace_private_json(receipt_path, receipt)
        for item_name in receipt["results"]:
            export_name = f"whisper-qualification-{qualification_id}-{len(exports)}.json"
            record_bytes = (state_dir / item_name).read_bytes()
            _export(document["operator_dir_fd"], operator_uid, export_name, record_bytes)
            exports.append(export_name)
        safe_receipt = {key: receipt.get(key) for key in (
            "schema_version", "qualification_id", "state", "stage", "revision", "inputs",
            "failure", "rollback_failure", "rollback", "results", "timings", "finished_unix")}
        safe_name = "whisper-qualification-receipt-" + qualification_id + ".json"
        export_bytes = json.dumps(safe_receipt, sort_keys=True, separators=(",", ":")).encode()
        _export(document["operator_dir_fd"], operator_uid, safe_name, export_bytes)
        os.close(document["operator_dir_fd"])
        for watched, handler in original_signal_handlers.items():
            signal.signal(watched, handler)
    outcome = {"qualification_id": qualification_id, "state": receipt["state"],
               "rollback": receipt["rollback"], "receipt": str(manifest.parent / safe_name),
               "results": [str(manifest.parent / name) for name in exports],
               "failure": receipt.get("failure"),
               "rollback_failure": receipt.get("rollback_failure")}
    return outcome


def _replace_private_json(path: Path, value):
    temporary = path.with_name(path.name + ".tmp")
    _write_json(temporary, value)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
