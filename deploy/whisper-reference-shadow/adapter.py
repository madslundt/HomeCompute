#!/usr/bin/env python3
"""Loopback-only reference diagnostic; no production route or runtime downloads."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("reference_protocol", ROOT / "protocol.py")
CODEC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CODEC)
PIN = json.loads((ROOT / "model-pin.json").read_text())


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def read_key(path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or \
                info.st_mode & 0o077 or not 16 <= info.st_size <= 512:
            raise RuntimeError("Invalid private credential")
        key = os.read(fd, 513).strip()
        if not 16 <= len(key) <= 512 or any(c <= 32 or c >= 127 for c in key):
            raise RuntimeError("Invalid private credential")
        return key
    finally:
        os.close(fd)


def checked_identity(environment: dict) -> dict:
    source = environment.get("HOME_COMPUTE_REVISION", "")
    image = environment.get("SHADOW_IMAGE_ID", "")
    if not re.fullmatch("[0-9a-f]{40}", source) or not re.fullmatch("sha256:[0-9a-f]{64}", image):
        raise RuntimeError("Missing immutable candidate identity")
    if not re.fullmatch("[0-9a-f]{64}", PIN["loader_sha256"]):
        raise RuntimeError("Unreviewed candidate loader")
    return {"homecompute_revision": source, "image_id": image}


def checked_engine_description(engine) -> dict:
    value = json.loads(json_bytes(engine.describe()))
    for name in ("model_sha256", "model_snapshot", "runtime", "runtime_commit", "loader_version", "loader_sha256"):
        if value.get(name) != PIN[name]:
            raise RuntimeError("Reference identity mismatch")
    if value.get("device") != "cuda:0" or value.get("persistent_tensor_bytes") != PIN["tensor_bytes"] or \
            value.get("persistent_tensor_count") != PIN["tensor_count"] or \
            value.get("persistent_dtype_counts") != {"float32": 744, "float16": 515} or \
            value.get("deterministic_algorithms") is not True:
        raise RuntimeError("Reference CUDA residency is unverified")
    if value.get("decoding_options") != PIN["decoding_defaults"]:
        raise RuntimeError("Reference decoding defaults mismatch")
    languages = value.get("language_codes")
    if not isinstance(languages, list) or len(languages) != 100 or len(set(languages)) != 100 or \
            not all(isinstance(code, str) and re.fullmatch("[a-z]{2,3}", code) for code in languages) or \
            not {"da", "en"}.issubset(languages):
        raise RuntimeError("Reference language inventory is unverified")
    return value


class ReferenceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, engine, key: bytes, identity: dict, address=("127.0.0.1", 8080)):
        if address[0] != "127.0.0.1":
            raise ValueError("Reference diagnostic is loopback only")
        self.engine, self.key, self.identity = engine, key, identity
        self.admission = threading.Lock()
        self.ready = True
        self.failure_category = None
        self.request_started = None
        self.engine_proof = checked_engine_description(engine)
        super().__init__(address, ReferenceHandler)

    def handle_error(self, request, client_address):
        pass  # No private request, transcript, exception value, or credential logs.


class ReferenceHandler(BaseHTTPRequestHandler):
    server: ReferenceServer
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def log_message(self, *args):
        pass

    def log_error(self, *args):
        pass

    def respond(self, status: int, value, *, content_type="application/json", provenance=False):
        payload = value if isinstance(value, bytes) else json_bytes(value)
        if len(payload) > CODEC.MAX_RESPONSE_BYTES:
            raise RuntimeError("Reference response exceeds bound")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if provenance:
            fields = {"Model-SHA256": PIN["model_sha256"], "Model-Snapshot": PIN["model_snapshot"],
                      "Runtime": PIN["runtime"], "Runtime-Commit": PIN["runtime_commit"],
                      "HomeCompute-Revision": self.server.identity["homecompute_revision"],
                      "Image-ID": self.server.identity["image_id"], "Device": "cuda"}
            for name, item in fields.items():
                self.send_header("X-HomeCompute-STT-" + name, item)
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != "/health":
            self.respond(404, {"error": "Not found"})
            return
        # Read only tensor metadata; no GPU operation competes with inference.
        if self.server.ready:
            try:
                self.server.engine_proof = checked_engine_description(self.server.engine)
            except Exception as error:
                self.server.ready = False
                self.server.failure_category = type(error).__name__
        proof = self.server.engine_proof
        result = {**self.server.identity,
                  **{key: PIN[key] for key in ("model_sha256", "model_snapshot", "runtime", "runtime_commit",
                                               "loader_version", "loader_sha256", "decoding_defaults")},
                  "status": "ok" if self.server.ready else "unavailable",
                  "inference_busy": self.server.admission.locked(),
                  "request_started_monotonic": self.server.request_started,
                  "failure_category": self.server.failure_category,
                  "device": "cuda", "gpu_model_buffer_bytes": proof["persistent_tensor_bytes"],
                  "max_audio_seconds": CODEC.MAX_AUDIO_SECONDS, "max_upload_bytes": CODEC.MAX_BODY_BYTES,
                  "resident_proof": proof, "production_qualified": False}
        self.respond(200 if self.server.ready else 503, result)

    def do_POST(self):
        if self.path != "/v1/audio/transcriptions":
            self.respond(404, {"error": "Not found"})
            return
        authorization = self.headers.get_all("Authorization", [])
        if len(authorization) != 1 or not authorization[0].startswith("Bearer ") or \
                not secrets.compare_digest(authorization[0][7:].encode("utf-8"), self.server.key):
            self.respond(401, {"error": "Unauthorized"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        types = self.headers.get_all("Content-Type", [])
        if self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1 or \
                not re.fullmatch("[0-9]{1,7}", lengths[0]) or len(types) != 1:
            self.respond(400, {"error": "Invalid request"})
            return
        length = int(lengths[0])
        if not 0 < length <= CODEC.MAX_BODY_BYTES:
            self.respond(413, {"error": "Request exceeds bound"})
            return
        if not self.server.admission.acquire(blocking=False):
            self.respond(409, {"error": "Reference engine busy"})
            return
        self.server.request_started = time.monotonic()
        try:
            if not self.server.ready:
                self.respond(503, {"error": "Reference engine unavailable"})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise CODEC.InvalidRequest("Invalid request")
            response_format, language, pcm, duration = CODEC.parse_request(body, types[0])
            if language is not None and language not in self.server.engine_proof["language_codes"]:
                raise CODEC.InvalidRequest("Invalid request")
            result = self.server.engine.transcribe_pcm(pcm, language=language)
            normalized = CODEC.normalize_result(result, duration)
            if response_format == "text":
                self.respond(200, normalized["text"].encode("utf-8"), content_type="text/plain; charset=utf-8", provenance=True)
            elif response_format == "json":
                self.respond(200, {"text": normalized["text"]}, provenance=True)
            else:
                self.respond(200, normalized, provenance=True)
        except CODEC.InvalidRequest:
            self.respond(400, {"error": "Invalid request"})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception as error:
            self.server.ready = False
            self.server.failure_category = type(error).__name__
            try:
                self.respond(500, {"error": "Reference inference failed", "category": type(error).__name__})
            except (OSError, ValueError):
                pass
        finally:
            self.server.request_started = None
            self.server.admission.release()


def main():
    identity = checked_identity(os.environ)
    key = read_key(os.environ.get("SHADOW_KEY_FILE", "/tmp/shadow.key"))
    model_file = os.environ.get("SHADOW_MODEL_FILE", "/models/ggml-large-v3.bin")
    # Source modules are explicitly selected; -I does not add their directory to sys.path.
    sys.path.insert(0, str(ROOT))
    from engine import ReferenceEngine
    engine = ReferenceEngine(model_file)
    engine.load()
    ReferenceServer(engine, key, identity).serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Reference diagnostic startup failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
