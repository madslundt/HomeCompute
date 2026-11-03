#!/usr/bin/env python3
"""Private, bounded OpenAI-compatible adapter around pinned whisper.cpp."""
from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import importlib.util
import json
import math
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
import wave

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


RUNTIME_COMMIT = "f049fff95a089aa9969deb009cdd4892b3e74916"
MODEL_ID = "whisper-large-v3"
ALIASES = {"whisper", MODEL_ID, "stt-danish", "whisper-turbo"}
MAX_AUDIO_SECONDS = 180
MAX_AUDIO_BYTES = MAX_AUDIO_SECONDS * 16_000 * 2 + 44
MAX_BODY_BYTES = MAX_AUDIO_BYTES + 128_000
MAX_CONFIGURED_AUDIO_SECONDS = 7_200
MAX_CONFIGURED_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_RESPONSE_BYTES = 8_000_000
CONTROL_TOKEN = re.compile(r"<\|[^|\r\n]{1,80}\|>")
FIELD_NAME = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,80}$")
ALLOWED_FIELDS = {"model", "response_format", "language", "prompt", "temperature",
                  "temperature_inc", "no_timestamps", "max_context",
                  "translate", "carry_initial_prompt"}
NATIVE_PORT = 8081


class RequestError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


@dataclass(frozen=True)
class CandidateConfig:
    model_path: Path
    key_path: Path
    max_audio_seconds: int = MAX_AUDIO_SECONDS
    max_upload_bytes: int = 6_000_000
    upload_timeout_seconds: int = 120
    native_timeout_seconds: int = 900
    native_threads: int = 4


def _bounded_env(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    if not raw.isdecimal() or not 1 <= int(raw) <= maximum:
        raise ValueError(f"invalid {name}")
    return int(raw)


def load_config() -> CandidateConfig:
    model_path = Path(os.environ.get("WHISPER_CPP_MODEL_PATH", "/models/ggml-large-v3.bin"))
    key_path = Path(os.environ.get("WHISPER_CPP_API_KEY_FILE", "/run/secrets/whisper_cpp_api_key"))
    max_audio_seconds = _bounded_env("WHISPER_CPP_MAX_AUDIO_SECONDS", MAX_AUDIO_SECONDS,
                                     MAX_CONFIGURED_AUDIO_SECONDS)
    max_upload_bytes = _bounded_env("WHISPER_CPP_MAX_UPLOAD_BYTES", 6_000_000,
                                    MAX_CONFIGURED_UPLOAD_BYTES)
    if max_upload_bytes < max_audio_seconds * 32_000 + 44:
        raise ValueError("WHISPER_CPP_MAX_UPLOAD_BYTES is too small for configured audio duration")
    return CandidateConfig(
        model_path=model_path,
        key_path=key_path,
        max_audio_seconds=max_audio_seconds,
        max_upload_bytes=max_upload_bytes,
        upload_timeout_seconds=_bounded_env("WHISPER_CPP_UPLOAD_TIMEOUT_SECONDS", 120, 1800),
        native_timeout_seconds=_bounded_env("WHISPER_CPP_NATIVE_TIMEOUT_SECONDS", 900, 14_400),
        native_threads=_bounded_env("WHISPER_CPP_NATIVE_THREADS", 4, 8),
    )


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


def parse_multipart(body: bytes, content_type: str, *, max_body_bytes: int = MAX_BODY_BYTES) -> tuple[dict[str, str], bytes]:
    if len(body) > max_body_bytes or "\r" in content_type or "\n" in content_type or len(content_type) > 512:
        raise RequestError(413 if len(body) > max_body_bytes else 400, "Invalid or oversized transcription request")
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            ("MIME-Version: 1.0\r\nContent-Type: " + content_type + "\r\n\r\n").encode("ascii") + body
        )
    except (ValueError, UnicodeError):
        raise RequestError(400, "Invalid multipart transcription request") from None
    boundary = message.get_boundary()
    if message.get_content_type() != "multipart/form-data" or not message.is_multipart() or \
            not boundary or len(boundary) > 70 or message.defects:
        raise RequestError(400, "Invalid multipart transcription request")
    fields: dict[str, str] = {}
    audio_items: list[bytes] = []
    for index, part in enumerate(message.iter_parts()):
        if index >= 64 or part.is_multipart() or part.defects or part.get_content_disposition() != "form-data":
            raise RequestError(400, "Invalid multipart transcription request")
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not FIELD_NAME.fullmatch(name):
            raise RequestError(400, "Invalid multipart transcription request")
        if part.get("Content-Transfer-Encoding", "binary").lower() not in {"binary", "7bit", "8bit"}:
            raise RequestError(400, "Invalid multipart transcription request")
        value = part.get_payload(decode=True)
        if not isinstance(value, bytes):
            raise RequestError(400, "Invalid multipart transcription request")
        if name == "file":
            if part.get_filename() is None or not value:
                raise RequestError(400, "Exactly one nonempty audio file is required")
            audio_items.append(value)
            continue
        if part.get_filename() is not None:
            raise RequestError(400, "Invalid transcription parameter")
        if name not in ALLOWED_FIELDS and name != "timestamp_granularities[]":
            raise RequestError(400, "Unsupported transcription parameter")
        if len(value) > 16_384:
            raise RequestError(400, "Invalid transcription parameter")
        try:
            decoded = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RequestError(400, "Invalid transcription parameter") from None
        if name == "timestamp_granularities[]":
            fields.setdefault(name, "")
            fields[name] += ("\n" if fields[name] else "") + decoded
        elif name in fields:
            raise RequestError(400, "Duplicate transcription parameter")
        else:
            fields[name] = decoded
    if len(audio_items) != 1:
        raise RequestError(400, "Exactly one audio file is required")
    return fields, audio_items[0]


def validate_audio(audio: bytes, max_seconds: int) -> float:
    if len(audio) > max_seconds * 32_000 + 44:
        raise RequestError(413, "Audio duration exceeds the limit")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if (source.getcomptype() != "NONE" or source.getnchannels() != 1 or
                    source.getsampwidth() != 2 or source.getframerate() != 16_000):
                raise RequestError(400, "Audio must be mono 16 kHz PCM16 WAV")
            frames = source.getnframes()
            if not 0 < frames <= max_seconds * 16_000:
                raise RequestError(413 if frames > max_seconds * 16_000 else 400,
                                   "Audio duration is outside the supported range")
            return frames / 16_000
    except (wave.Error, EOFError, OSError):
        raise RequestError(400, "Invalid PCM WAV audio") from None


def normalize_fields(fields: dict[str, str]) -> tuple[str, list[tuple[str, str]]]:
    model = fields.get("model", "")
    if model not in ALIASES:
        raise RequestError(400, "Unsupported transcription model")
    response_format = fields.get("response_format", "json")
    if response_format not in {"text", "json", "verbose_json"}:
        raise RequestError(400, "Only text, json and verbose_json response formats are supported")
    if "timestamp_granularities[]" in fields:
        granularities = fields["timestamp_granularities[]"].split("\n")
        if len(set(granularities)) != len(granularities) or any(g not in {"segment", "word"} for g in granularities):
            raise RequestError(400, "Invalid timestamp granularity")
        if "word" in granularities:
            raise RequestError(422, "Word timestamps are unsupported; only native segment timestamps are available")
        if response_format != "verbose_json" and granularities:
            raise RequestError(400, "Segment timestamps require verbose_json")
        if "segment" in granularities and fields.get("no_timestamps", "false").lower() in {"true", "1"}:
            raise RequestError(400, "Segment timestamps conflict with no_timestamps")
    native: list[tuple[str, str]] = [("response_format", response_format)]
    language = fields.get("language", "auto")
    if language != "auto" and not re.fullmatch(r"[A-Za-z]{2,3}", language):
        raise RequestError(400, "Unsupported language value")
    native.append(("language", language.lower()))
    if "prompt" in fields:
        if "\x00" in fields["prompt"]:
            raise RequestError(400, "Invalid transcription parameter")
        native.append(("prompt", fields["prompt"]))
    for name in ("temperature", "temperature_inc"):
        if name in fields:
            try:
                number = float(fields[name])
            except ValueError:
                raise RequestError(400, "Invalid decoding parameter") from None
            if not math.isfinite(number) or not 0.0 <= number <= 1.0:
                raise RequestError(400, "Invalid decoding parameter")
            native.append((name, str(number)))
    for name in ("no_timestamps", "translate", "carry_initial_prompt"):
        if name in fields:
            value = fields[name].lower()
            if value not in {"true", "false", "1", "0"}:
                raise RequestError(400, "Invalid decoding parameter")
            native.append((name, "true" if value in {"true", "1"} else "false"))
    if "max_context" in fields:
        try:
            value = int(fields["max_context"])
        except ValueError:
            raise RequestError(400, "Invalid decoding parameter") from None
        if not 0 <= value <= 448:
            raise RequestError(400, "Invalid decoding parameter")
        native.append(("max_context", str(value)))
    return response_format, native


def make_native_multipart(fields: list[tuple[str, str]], audio: bytes) -> tuple[bytes, str]:
    boundary = "hc-whisper-cpp-" + secrets.token_hex(16)
    parts: list[bytes] = []
    for name, value in fields:
        if not FIELD_NAME.fullmatch(name):
            raise RequestError(400, "Invalid transcription parameter")
        parts.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                      value.encode("utf-8"), b"\r\n"))
    parts.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                  audio, f"\r\n--{boundary}--\r\n".encode()))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        raise RequestError(502, "Speech backend returned invalid text")
    return CONTROL_TOKEN.sub("", value)


def normalize_native_response(payload: bytes, response_format: str, duration: float) -> tuple[bytes, str]:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RequestError(502, "Speech backend response is too large")
    if response_format == "text":
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RequestError(502, "Speech backend returned invalid text") from None
        return CONTROL_TOKEN.sub("", text).encode("utf-8"), "text/plain; charset=utf-8"
    if response_format != "verbose_json":
        try:
            result = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RequestError(502, "Speech backend returned invalid JSON") from None
        if not isinstance(result, dict):
            raise RequestError(502, "Speech backend returned invalid JSON")
        result["text"] = _clean_text(result.get("text"))
        return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(), "application/json"
    try:
        result = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RequestError(502, "Speech backend returned invalid JSON") from None
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        raise RequestError(502, "Speech backend returned invalid verbose JSON")
    result["text"] = _clean_text(result.get("text"))
    cursor = 0
    exact_serialization = True
    parts: list[str] = []
    for index, segment in enumerate(result["segments"]):
        if not isinstance(segment, dict):
            raise RequestError(502, "Speech backend returned an invalid segment")
        segment_text = _clean_text(segment.get("text"))
        segment["text"] = segment_text
        segment["kind"] = "segment"
        # whisper.cpp token objects are model tokens, not word alignments.
        segment.pop("words", None)
        start = segment.get("start")
        end = segment.get("end")
        if start is not None or end is not None:
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or \
                    not math.isfinite(start) or not math.isfinite(end) or \
                    start < 0 or end < start or end > duration:
                raise RequestError(502, "Speech backend returned out-of-bounds segment timestamps")
        parts.append(segment_text + "\n")
        begin, finish = cursor, cursor + len(segment_text)
        if result["text"][begin:finish] != segment_text or \
                (finish < len(result["text"]) and result["text"][finish:finish + 1] != "\n"):
            exact_serialization = False
        if "kind" in segment:
            segment["start_offset"] = begin
            segment["end_offset"] = finish
        cursor = finish + 1
    if exact_serialization and "".join(parts) == result["text"]:
        pass
    else:
        for segment in result["segments"]:
            segment.pop("start_offset", None)
            segment.pop("end_offset", None)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(), "application/json"


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

    def __init__(self, config: CandidateConfig, api_key: bytes, address=("0.0.0.0", 8000), native_process=None):
        self.config = config
        self.api_key = api_key
        self.native_process = native_process
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
            ready = self.server.native_process is None or self.server.native_process.poll() is None
            self.respond(200 if ready else 503, json.dumps({
                "status": "ok" if ready else "unavailable",
                "model": MODEL_ID,
                "model_repository": PIN["repository"],
                "model_snapshot": PIN["snapshot"],
                "model_sha256": PIN["sha256"],
                "runtime": "whisper.cpp",
                "runtime_commit": RUNTIME_COMMIT,
                "device": "cpu",
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


def _native_health(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{NATIVE_PORT}/health", timeout=timeout) as response:
            return response.status == 200 and json.loads(response.read(512)).get("status") == "ok"
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def native_command(config: CandidateConfig) -> list[str]:
    return [
        "/usr/local/bin/whisper-server", "--model", str(config.model_path), "--host", "127.0.0.1",
        "--port", str(NATIVE_PORT), "--no-gpu", "--threads", str(config.native_threads),
        "--max-context", "0", "--language", "auto", "--no-language-probabilities",
    ]


def run() -> int:
    config = load_config()
    model = verify_model(config.model_path)
    key = read_api_key(config.key_path)
    native = subprocess.Popen(native_command(config), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, close_fds=True)
    server = None
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if native.poll() is not None:
                return 1
            if _native_health():
                server = CandidateServer(config, key, native_process=native)
                # Force successful, checksum-bound startup before advertising readiness.
                print(json.dumps({"event": "ready", "model_sha256": model["sha256"],
                                  "runtime_commit": RUNTIME_COMMIT}, sort_keys=True), flush=True)
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
