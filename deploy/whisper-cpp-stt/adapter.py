#!/usr/bin/env python3
"""Private, bounded OpenAI-compatible adapter around pinned whisper.cpp."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

_VERIFY_SPEC = importlib.util.spec_from_file_location(
    "homecompute_whisper_cpp_verify_model", Path(__file__).with_name("verify_model.py"))
if _VERIFY_SPEC is None or _VERIFY_SPEC.loader is None:
    raise RuntimeError("candidate model verifier is unavailable")
_VERIFY_MODULE = importlib.util.module_from_spec(_VERIFY_SPEC)
sys.modules[_VERIFY_SPEC.name] = _VERIFY_MODULE
_VERIFY_SPEC.loader.exec_module(_VERIFY_MODULE)
ModelPinError = _VERIFY_MODULE.ModelPinError
PIN = _VERIFY_MODULE.PIN
verify_model = _VERIFY_MODULE.verify_model

_CODEC_SPEC = importlib.util.spec_from_file_location(
    "homecompute_whisper_cpp_contract", Path(__file__).with_name("contract.py"))
if _CODEC_SPEC is None or _CODEC_SPEC.loader is None:
    raise RuntimeError("candidate request codec is unavailable")
_CODEC_MODULE = importlib.util.module_from_spec(_CODEC_SPEC)
sys.modules[_CODEC_SPEC.name] = _CODEC_MODULE
_CODEC_SPEC.loader.exec_module(_CODEC_MODULE)
for _name in ("MODEL_ID", "ALIASES", "MAX_AUDIO_SECONDS", "MAX_BODY_BYTES",
              "MAX_CONFIGURED_AUDIO_SECONDS", "MAX_CONFIGURED_UPLOAD_BYTES",
              "MAX_RESPONSE_BYTES", "RequestError", "parse_multipart", "validate_audio",
              "normalize_fields", "make_native_multipart", "normalize_native_response"):
    globals()[_name] = getattr(_CODEC_MODULE, _name)


RUNTIME_COMMIT = "f049fff95a089aa9969deb009cdd4892b3e74916"
CUDA_PATCH_VERSION = "strict-gpu-request-determinism-v3"
CUDA_PATCHER_SHA256 = "0d91dddf479f120f2b421a8a1a61531d1c2675d4013f3d1e6d6fd15434bc62a1"
NATIVE_PORT = 8081


@dataclass(frozen=True)
class CandidateConfig:
    model_path: Path
    key_path: Path
    homecompute_revision: str = "0" * 40
    image_id: str = "sha256:" + "0" * 64
    max_audio_seconds: int = MAX_AUDIO_SECONDS
    max_upload_bytes: int = 6_000_000
    upload_timeout_seconds: int = 120
    native_timeout_seconds: int = 900
    native_threads: int = 4
    device: str = "cpu"


def _bounded_env(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    if not raw.isdecimal() or not 1 <= int(raw) <= maximum:
        raise ValueError(f"invalid {name}")
    return int(raw)


def load_config() -> CandidateConfig:
    model_path = Path(os.environ.get("WHISPER_CPP_MODEL_PATH", "/models/ggml-large-v3.bin"))
    key_path = Path(os.environ.get("WHISPER_CPP_API_KEY_FILE", "/run/secrets/whisper_cpp_api_key"))
    homecompute_revision = os.environ.get("WHISPER_CPP_HOME_COMPUTE_REVISION", "")
    image_id = os.environ.get("WHISPER_CPP_IMAGE_ID", "")
    if not re.fullmatch(r"[0-9a-f]{40}", homecompute_revision):
        raise ValueError("invalid HomeCompute source revision")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("invalid candidate image ID")
    max_audio_seconds = _bounded_env("WHISPER_CPP_MAX_AUDIO_SECONDS", MAX_AUDIO_SECONDS,
                                     MAX_CONFIGURED_AUDIO_SECONDS)
    max_upload_bytes = _bounded_env("WHISPER_CPP_MAX_UPLOAD_BYTES", 6_000_000,
                                    MAX_CONFIGURED_UPLOAD_BYTES)
    if max_upload_bytes < max_audio_seconds * 32_000 + 44:
        raise ValueError("WHISPER_CPP_MAX_UPLOAD_BYTES is too small for configured audio duration")
    device = os.environ.get("WHISPER_CPP_DEVICE", "cpu")
    if device not in {"cpu", "cuda"}:
        raise ValueError("invalid WHISPER_CPP_DEVICE")
    return CandidateConfig(
        model_path=model_path,
        key_path=key_path,
        homecompute_revision=homecompute_revision,
        image_id=image_id,
        max_audio_seconds=max_audio_seconds,
        max_upload_bytes=max_upload_bytes,
        upload_timeout_seconds=_bounded_env("WHISPER_CPP_UPLOAD_TIMEOUT_SECONDS", 120, 1800),
        native_timeout_seconds=_bounded_env("WHISPER_CPP_NATIVE_TIMEOUT_SECONDS", 900, 14_400),
        native_threads=_bounded_env("WHISPER_CPP_NATIVE_THREADS", 4, 8),
        device=device,
    )


def require_cuda_device() -> None:
    """Fail closed instead of silently running a CUDA candidate on CPU."""
    try:
        ctypes.CDLL("libcuda.so.1")
    except OSError:
        raise RuntimeError("CUDA driver library is unavailable") from None
    try:
        runtime = ctypes.CDLL("libcudart.so.13")
        count = ctypes.c_int()
        status = runtime.cudaGetDeviceCount(ctypes.byref(count))
    except (OSError, AttributeError):
        raise RuntimeError("CUDA runtime is unavailable") from None
    if status != 0 or count.value < 1:
        raise RuntimeError("CUDA device is unavailable")


def read_api_key(path: Path) -> bytes:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size < 16 or
                info.st_size > 512 or stat.S_IMODE(info.st_mode) & 0o337):
            raise ValueError
        key = path.read_bytes().strip()
    except (OSError, ValueError):
        raise RuntimeError("candidate API credential is unavailable or invalid") from None
    if not key or b"\n" in key or b"\r" in key:
        raise RuntimeError("candidate API credential is unavailable or invalid")
    return key


def _native_request(body: bytes, content_type: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        f"http://127.0.0.1:{NATIVE_PORT}/inference", data=body,
        headers={"Content-Type": content_type, "User-Agent": "homecompute-whisper-cpp/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RequestError(502, "Speech backend rejected the request")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise RequestError(502, "Speech backend response is too large")
            return payload
    except urllib.error.HTTPError:
        raise RequestError(502, "Speech backend rejected the request") from None
    except (TimeoutError, urllib.error.URLError, OSError):
        raise RequestError(502, "Speech backend is unavailable") from None


class CandidateServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, config: CandidateConfig, api_key: bytes, address=("0.0.0.0", 8000),
                 native_process=None, native_health=None):
        self.config = config
        self.api_key = api_key
        self.native_process = native_process
        self.native_health = native_health if isinstance(native_health, dict) else {}
        self.native_gpu_proof = {key: self.native_health[key] for key in
                                 ("compute_backend", "gpu_model_buffer_bytes") if key in self.native_health}
        self.inflight = threading.BoundedSemaphore(1)
        super().__init__(address, CandidateHandler)

    def handle_error(self, request, client_address):
        # Never let request data or exception messages enter logs.
        pass


class CandidateHandler(BaseHTTPRequestHandler):
    server: CandidateServer

    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def log_message(self, *args):
        pass

    def log_error(self, *args):
        pass

    def respond(self, status: int, payload: bytes, content_type: str = "application/json", *, provenance: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if provenance:
            self.send_header("X-HomeCompute-STT-Model-SHA256", PIN["sha256"])
            self.send_header("X-HomeCompute-STT-Model-Snapshot", PIN["snapshot"])
            self.send_header("X-HomeCompute-STT-Runtime", "whisper.cpp")
            self.send_header("X-HomeCompute-STT-Runtime-Commit", RUNTIME_COMMIT)
            self.send_header("X-HomeCompute-STT-HomeCompute-Revision", self.server.config.homecompute_revision)
            self.send_header("X-HomeCompute-STT-Image-ID", self.server.config.image_id)
            self.send_header("X-HomeCompute-STT-Device", self.server.config.device)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def _authenticate(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or not values[0].startswith("Bearer "):
            raise RequestError(401, "Unauthorized")
        supplied = values[0][7:].encode("utf-8", errors="strict")
        if not secrets.compare_digest(supplied, self.server.api_key):
            raise RequestError(401, "Unauthorized")

    def do_GET(self):
        if self.path == "/health":
            process_ready = self.server.native_process is None or self.server.native_process.poll() is None
            current_native = _native_health(timeout=1.0)
            native_health = None
            if isinstance(current_native, dict):
                native_health = current_native
                if self.server.config.device == "cpu":
                    self.server.native_health = current_native
                elif (self.server.config.device == "cuda" and
                        type(current_native.get("inference_busy")) is bool and current_native["inference_busy"]):
                    native_health = {**self.server.native_gpu_proof, **current_native}
            ready = process_ready and native_health_is_valid(self.server.config, native_health)
            idle_probe = self.server.inflight.acquire(blocking=False)
            native_busy = (native_health.get("inference_busy")
                           if isinstance(native_health, dict) else None)
            busy = (not idle_probe or native_busy is not False) if self.server.config.device == "cuda" else not idle_probe
            if idle_probe:
                self.server.inflight.release()
            self.respond(200 if ready else 503, json.dumps({
                "status": "ok" if ready else "unavailable",
                "inference_busy": busy,
                "native_inference_busy": native_busy,
                "model": MODEL_ID,
                "model_repository": PIN["repository"],
                "model_snapshot": PIN["snapshot"],
                "model_sha256": PIN["sha256"],
                "runtime": "whisper.cpp",
                "runtime_commit": RUNTIME_COMMIT,
                "homecompute_revision": self.server.config.homecompute_revision,
                "image_id": self.server.config.image_id,
                "device": self.server.config.device,
                "compute_backend": native_health.get("compute_backend", "cpu") if native_health else "unknown",
                "gpu_model_buffer_bytes": native_health.get("gpu_model_buffer_bytes", 0) if native_health else 0,
                "strict_gpu_patch": CUDA_PATCH_VERSION if self.server.config.device == "cuda" else None,
                "strict_gpu_patcher_sha256": CUDA_PATCHER_SHA256 if self.server.config.device == "cuda" else None,
                "decoding_defaults": {
                    "language": "auto", "prompt": None, "max_context": 0,
                    "strategy": "beam_search", "beam_size": 5, "best_of": 5,
                    "temperature": 0.0, "temperature_inc": 0.2,
                    "entropy_thold": 2.4, "logprob_thold": -1.0,
                    "max_len": -1,
                    "no_context": True, "translate": False,
                    "carry_initial_prompt": False,
                },
                "max_context": 0,
                "max_audio_seconds": self.server.config.max_audio_seconds,
                "max_upload_bytes": self.server.config.max_upload_bytes,
                "upload_timeout_seconds": self.server.config.upload_timeout_seconds,
                "native_timeout_seconds": self.server.config.native_timeout_seconds,
            }, sort_keys=True).encode())
            return
        if self.path != "/v1/models":
            self.respond(404, b'{"error":"Not found"}')
            return
        try:
            self._authenticate()
        except RequestError as error:
            self.respond(error.status, json.dumps({"error": error.message}).encode())
            return
        self.respond(200, json.dumps({"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}).encode())

    def do_POST(self):
        if self.path != "/v1/audio/transcriptions":
            self.respond(404, b'{"error":"Not found"}')
            return
        acquired = False
        try:
            self._authenticate()
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdecimal():
                raise RequestError(400, "A bounded Content-Length is required")
            length = int(lengths[0])
            max_body_bytes = self.server.config.max_upload_bytes + 128_000
            if length > max_body_bytes:
                raise RequestError(413, "Transcription request is too large")
            if not self.server.inflight.acquire(blocking=False):
                raise RequestError(429, "Speech service is busy")
            acquired = True
            if self.server.config.device == "cuda":
                native_health = _native_health(timeout=1.0)
                if (not isinstance(native_health, dict) or native_health.get("status") != "ok" or
                        type(native_health.get("inference_busy")) is not bool):
                    raise RequestError(503, "Native inference state is unavailable")
                if native_health["inference_busy"]:
                    raise RequestError(429, "Speech service is busy")
                if not native_health_is_valid(self.server.config, native_health):
                    raise RequestError(503, "Native inference state is unavailable")
            self.request.settimeout(self.server.config.upload_timeout_seconds)
            body = self.rfile.read(length)
            if len(body) != length:
                raise RequestError(400, "Incomplete transcription request")
            fields, audio = parse_multipart(body, self.headers.get("Content-Type", ""),
                                            max_body_bytes=max_body_bytes)
            duration = validate_audio(audio, self.server.config.max_audio_seconds)
            response_format, native_fields = normalize_fields(fields)
            self.request.settimeout(self.server.config.native_timeout_seconds + 60)
            native_body, native_type = make_native_multipart(native_fields, audio)
            raw = _native_request(native_body, native_type, self.server.config.native_timeout_seconds)
            payload, content_type = normalize_native_response(raw, response_format, duration)
            self.respond(200, payload, content_type, provenance=True)
        except RequestError as error:
            self.respond(error.status, json.dumps({"error": error.message}).encode())
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            if acquired:
                self.server.inflight.release()


def native_health_is_valid(config: CandidateConfig, health: object) -> bool:
    if not isinstance(health, dict) or health.get("status") != "ok":
        return False
    if config.device == "cpu":
        return True
    gpu_bytes = health.get("gpu_model_buffer_bytes")
    return (health.get("compute_backend") == "cuda" and isinstance(gpu_bytes, int) and
            not isinstance(gpu_bytes, bool) and gpu_bytes > 0 and
            type(health.get("inference_busy")) is bool)


def _native_health(timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{NATIVE_PORT}/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            value = json.loads(response.read(512))
            return value if isinstance(value, dict) else None
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def native_command(config: CandidateConfig) -> list[str]:
    command = [
        "/usr/local/bin/whisper-server", "--model", str(config.model_path), "--host", "127.0.0.1",
        "--port", str(NATIVE_PORT), "--threads", str(config.native_threads),
        "--max-context", "0", "--language", "auto", "--no-language-probabilities",
    ]
    if config.device == "cpu":
        command.append("--no-gpu")
    else:
        command.extend(("--device", "0"))
    return command


def run() -> int:
    config = load_config()
    model = verify_model(config.model_path)
    key = read_api_key(config.key_path)
    if config.device == "cuda":
        require_cuda_device()
    native = subprocess.Popen(native_command(config), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, close_fds=True)
    server = None
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if native.poll() is not None:
                return 1
            native_health = _native_health()
            if native_health_is_valid(config, native_health):
                server = CandidateServer(config, key, native_process=native, native_health=native_health)
                # Force successful, checksum-bound startup before advertising readiness.
                print(json.dumps({"event": "ready", "model_sha256": model["sha256"],
                                  "runtime_commit": RUNTIME_COMMIT,
                                  "device": config.device,
                                  "compute_backend": native_health.get("compute_backend", "cpu"),
                                  "gpu_model_buffer_bytes": native_health.get("gpu_model_buffer_bytes", 0),
                                  "strict_gpu_patch": CUDA_PATCH_VERSION if config.device == "cuda" else None},
                                 sort_keys=True), flush=True)
                server.serve_forever(poll_interval=0.25)
                return 0
            time.sleep(0.25)
        return 1
    finally:
        if server is not None:
            server.server_close()
        if native.poll() is None:
            native.terminate()
            try:
                native.wait(timeout=10)
            except subprocess.TimeoutExpired:
                native.kill()
                native.wait(timeout=5)


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (ModelPinError, RuntimeError, ValueError) as exc:
        # Print only a fixed exception category. Never echo user-provided data.
        print("Whisper.cpp candidate startup failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
