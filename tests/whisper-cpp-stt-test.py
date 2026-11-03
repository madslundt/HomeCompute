#!/usr/bin/env python3
"""Synthetic contract and pinned-artifact refusal tests for the whisper.cpp candidate."""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
import hashlib
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import urllib.error
import urllib.request
import wave


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/whisper-cpp-stt"


def load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = load_file("verify_model", DEPLOY / "verify_model.py")
ADAPTER = load_file("whisper_cpp_adapter", DEPLOY / "adapter.py")
CUDA_PATCHER = load_file("whisper_cpp_cuda_patcher", DEPLOY / "patch-whisper-cpp-cuda.py")
LIFECYCLE = load_file("whisper_cpp_lifecycle", ROOT / "scripts/setup-whisper-stt.py")


def synthetic_model(header_overrides: dict[str, int] | None = None) -> bytes:
    values = {
        "n_vocab": 51866, "n_audio_ctx": 1500, "n_audio_state": 1280, "n_audio_head": 20,
        "n_audio_layer": 32, "n_text_ctx": 448, "n_text_state": 1280, "n_text_head": 20,
        "n_text_layer": 32, "n_mels": 128, "ftype": 1,
    }
    values.update(header_overrides or {})
    return struct.pack("<12i", VERIFY.MAGIC_LE, *(values[k] for k in (
        "n_vocab", "n_audio_ctx", "n_audio_state", "n_audio_head", "n_audio_layer",
        "n_text_ctx", "n_text_state", "n_text_head", "n_text_layer", "n_mels", "ftype")))


def wav_bytes(seconds: float = 0.1, *, channels: int = 1, sample_rate: int = 16_000,
              sample_width: int = 2) -> bytes:
    frame_count = int(seconds * sample_rate)
    with io.BytesIO() as target:
        with wave.open(target, "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(sample_width)
            output.setframerate(sample_rate)
            output.writeframes(b"\0" * frame_count * channels * sample_width)
        return target.getvalue()


def multipart(fields: list[tuple[str, str]], audio: bytes, *, boundary="test-boundary"):
    chunks = []
    for name, value in fields:
        chunks.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                       value.encode(), b"\r\n"))
    chunks.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                   audio, f"\r\n--{boundary}--\r\n".encode()))
    return b"".join(chunks), "multipart/form-data; boundary=" + boundary


class ModelVerifierTests(unittest.TestCase):
    def test_accepts_only_exact_test_pin_and_full_large_header(self):
        model = synthetic_model()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            path.write_bytes(model)
            result = VERIFY.verify_model(path, expected_sha256=hashlib.sha256(model).hexdigest(),
                                         expected_size=len(model))
        self.assertEqual(result["header"]["n_audio_layer"], 32)
        self.assertEqual(result["header"]["n_text_layer"], 32)
        self.assertEqual(result["header"]["n_mels"], 128)
        self.assertEqual(result["header"]["ftype"], 1)

    def test_refuses_turbo_or_quantized_headers_before_digest_acceptance(self):
        for overrides, expected in [({"n_audio_layer": 4}, "n_audio_layer"), ({"ftype": 8}, "ftype")]:
            model = synthetic_model(overrides)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "model.bin"
                path.write_bytes(model)
                with self.assertRaisesRegex(VERIFY.ModelPinError, expected):
                    VERIFY.verify_model(path, expected_sha256=hashlib.sha256(model).hexdigest(),
                                        expected_size=len(model))

    def test_refuses_checksum_size_and_symlink_mismatch(self):
        model = synthetic_model()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "model.bin"
            path.write_bytes(model)
            with self.assertRaisesRegex(VERIFY.ModelPinError, "checksum"):
                VERIFY.verify_model(path, expected_sha256="0" * 64, expected_size=len(model))
            with self.assertRaisesRegex(VERIFY.ModelPinError, "size"):
                VERIFY.verify_model(path, expected_sha256=hashlib.sha256(model).hexdigest(),
                                    expected_size=len(model) + 1)
            link = root / "link.bin"
            link.symlink_to(path)
            with self.assertRaisesRegex(VERIFY.ModelPinError, "non-symlink"):
                VERIFY.verify_model(link, expected_sha256=hashlib.sha256(model).hexdigest(),
                                    expected_size=len(model))

    def test_candidate_api_key_must_be_a_restricted_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_bytes(b"synthetic-test-key-32-bytes-long")
            path.chmod(0o440)
            self.assertEqual(ADAPTER.read_api_key(path), b"synthetic-test-key-32-bytes-long")
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "credential is unavailable"):
                ADAPTER.read_api_key(path)

class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.provenance_env = patch.dict(os.environ, {
            "WHISPER_CPP_HOME_COMPUTE_REVISION": "a" * 40,
            "WHISPER_CPP_IMAGE_ID": "sha256:" + "b" * 64,
        }, clear=False)
        self.provenance_env.start()
        self.addCleanup(self.provenance_env.stop)

    def test_preserves_prompt_language_format_and_supported_decoding_options(self):
        fields = {"model": "whisper", "response_format": "verbose_json", "language": "da",
                  "prompt": "synthetic neutral\nprompt", "temperature": "0.2", "max_context": "0",
                  "beam_size": "5", "best_of": "5", "timestamp_granularities[]": "segment"}
        format_name, forwarded = ADAPTER.normalize_fields(fields)
        self.assertEqual(format_name, "verbose_json")
        self.assertIn(("language", "da"), forwarded)
        self.assertIn(("prompt", "synthetic neutral\nprompt"), forwarded)
        self.assertIn(("temperature", "0.2"), forwarded)
        self.assertIn(("max_context", "0"), forwarded)
        self.assertIn(("beam_size", "5"), forwarded)
        self.assertIn(("best_of", "5"), forwarded)
        _, defaults = ADAPTER.normalize_fields({"model": "whisper"})
        self.assertIn(("beam_size", "5"), defaults)
        self.assertIn(("best_of", "5"), defaults)
        _, greedy = ADAPTER.normalize_fields({"model": "whisper", "beam_size": "-1", "best_of": "1"})
        self.assertIn(("beam_size", "-1"), greedy)
        self.assertIn(("best_of", "1"), greedy)

    def test_rejects_unsupported_fields_and_word_timestamps(self):
        body, content_type = multipart([("model", "whisper"), ("initial_prompt", "unmapped")], wav_bytes())
        with self.assertRaisesRegex(ADAPTER.RequestError, "Unsupported"):
            ADAPTER.parse_multipart(body, content_type)
        with self.assertRaisesRegex(ADAPTER.RequestError, "Word timestamps are unsupported"):
            ADAPTER.normalize_fields({"model": "whisper", "response_format": "verbose_json",
                                      "timestamp_granularities[]": "word"})
        with self.assertRaisesRegex(ADAPTER.RequestError, "Only text, json"):
            ADAPTER.normalize_fields({"model": "whisper", "response_format": "srt"})
        for name, value in (("beam_size", "0"), ("beam_size", "17"), ("best_of", "0"), ("best_of", "17")):
            with self.assertRaisesRegex(ADAPTER.RequestError, "Invalid decoding"):
                ADAPTER.normalize_fields({"model": "whisper", name: value})

    def test_bounds_exact_pcm_wav_contract_without_resampling_or_truncation(self):
        self.assertEqual(ADAPTER.validate_audio(wav_bytes(), 180), 0.1)
        for wrong in (wav_bytes(channels=2), wav_bytes(sample_rate=8000), wav_bytes(sample_width=1)):
            with self.assertRaises(ADAPTER.RequestError):
                ADAPTER.validate_audio(wrong, 180)
        with self.assertRaisesRegex(ADAPTER.RequestError, "exceeds"):
            ADAPTER.validate_audio(wav_bytes(180.01), 180)

    def test_truncated_wav_payload_is_rejected_before_native_forwarding(self):
        audio = wav_bytes()
        self.assertGreater(len(audio), 44)
        truncated = audio[:44]  # RIFF header still claims 1,600 PCM frames.
        with self.assertRaisesRegex(ADAPTER.RequestError, "payload is truncated"):
            ADAPTER.validate_audio(truncated, 180)

        config = ADAPTER.CandidateConfig(
            Path("/unused/model"), Path("/unused/key"), homecompute_revision="a" * 40,
            image_id="sha256:" + "b" * 64, native_timeout_seconds=2)
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body, content_type = multipart([("model", "whisper")], truncated)
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions",
                data=body,
                headers={"Content-Type": content_type,
                         "Authorization": "Bearer synthetic-secret-value"},
            )
            with patch.object(ADAPTER, "_native_request", side_effect=AssertionError("must not forward")):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_longform_limits_require_explicit_consistent_bounded_configuration(self):
        with patch.dict(os.environ, {"WHISPER_CPP_MAX_AUDIO_SECONDS": "1200",
                                     "WHISPER_CPP_MAX_UPLOAD_BYTES": "40000000"}, clear=False):
            config = ADAPTER.load_config()
            self.assertEqual(config.max_audio_seconds, 1200)
            self.assertEqual(config.max_upload_bytes, 40_000_000)
        with patch.dict(os.environ, {"WHISPER_CPP_MAX_AUDIO_SECONDS": "7200",
                                     "WHISPER_CPP_MAX_UPLOAD_BYTES": "268435456",
                                     "WHISPER_CPP_NATIVE_TIMEOUT_SECONDS": "14400"}, clear=False):
            config = ADAPTER.load_config()
            self.assertEqual(config.max_audio_seconds, 7200)
            self.assertEqual(config.max_upload_bytes, 268_435_456)
            self.assertEqual(config.native_timeout_seconds, 14_400)
        with patch.dict(os.environ, {"WHISPER_CPP_MAX_AUDIO_SECONDS": "1200",
                                     "WHISPER_CPP_MAX_UPLOAD_BYTES": "6000000"}, clear=False):
            with self.assertRaisesRegex(ValueError, "too small"):
                ADAPTER.load_config()
        with patch.dict(os.environ, {"WHISPER_CPP_MAX_AUDIO_SECONDS": "7201",
                                     "WHISPER_CPP_MAX_UPLOAD_BYTES": "256000000"}, clear=False):
            with self.assertRaisesRegex(ValueError, "invalid"):
                ADAPTER.load_config()

    def test_provenance_requires_pinned_homecompute_revision_and_image_id(self):
        with patch.dict(os.environ, {"WHISPER_CPP_HOME_COMPUTE_REVISION": "bad",
                                     "WHISPER_CPP_IMAGE_ID": "sha256:" + "b" * 64}, clear=False):
            with self.assertRaisesRegex(ValueError, "source revision"):
                ADAPTER.load_config()
        with patch.dict(os.environ, {"WHISPER_CPP_HOME_COMPUTE_REVISION": "a" * 40,
                                     "WHISPER_CPP_IMAGE_ID": "not-a-digest"}, clear=False):
            with self.assertRaisesRegex(ValueError, "image ID"):
                ADAPTER.load_config()

    def test_compose_build_revision_is_baked_and_resolved_image_id_reaches_runtime(self):
        compose = (DEPLOY / "compose.yaml").read_text()
        dockerfile = (DEPLOY / "Dockerfile").read_text()
        self.assertIn("HOME_COMPUTE_REVISION: ${WHISPER_CPP_HOME_COMPUTE_REVISION:?", compose)
        self.assertIn("WHISPER_CPP_IMAGE_ID: ${WHISPER_CPP_IMAGE_ID:-}", compose)
        self.assertIn("ARG HOME_COMPUTE_REVISION", dockerfile)
        self.assertIn("WHISPER_CPP_HOME_COMPUTE_REVISION=${HOME_COMPUTE_REVISION}", dockerfile)
        self.assertIn('user: "10001:10001"', compose)
        with patch.dict(os.environ, {"WHISPER_CPP_HOME_COMPUTE_REVISION": "c" * 40,
                                     "WHISPER_CPP_IMAGE_ID": "sha256:" + "d" * 64}, clear=False):
            config = ADAPTER.load_config()
        self.assertEqual(config.homecompute_revision, "c" * 40)
        self.assertEqual(config.image_id, "sha256:" + "d" * 64)

    def test_native_process_is_pinned_to_cpu_auto_language_and_zero_context(self):
        config = ADAPTER.CandidateConfig(Path("/models/ggml-large-v3.bin"), Path("/key"), native_threads=4)
        command = ADAPTER.native_command(config)
        self.assertEqual(command[command.index("--model") + 1], "/models/ggml-large-v3.bin")
        self.assertIn("--no-gpu", command)
        self.assertEqual(command[command.index("--max-context") + 1], "0")
        self.assertEqual(command[command.index("--language") + 1], "auto")
        self.assertNotIn("--prompt", command)

    def test_cuda_device_is_explicit_and_never_silently_forced_to_cpu(self):
        cpu = ADAPTER.native_command(ADAPTER.CandidateConfig(
            Path("/models/ggml-large-v3.bin"), Path("/key")))
        cuda = ADAPTER.native_command(ADAPTER.CandidateConfig(
            Path("/models/ggml-large-v3.bin"), Path("/key"), device="cuda"))
        self.assertIn("--no-gpu", cpu)
        self.assertNotIn("--no-gpu", cuda)
        self.assertEqual(cuda[cuda.index("--device") + 1], "0")
        self.assertEqual(cuda[cuda.index("--max-context") + 1], "0")
        with patch.dict(os.environ, {"WHISPER_CPP_DEVICE": "cuda"}, clear=False):
            self.assertEqual(ADAPTER.load_config().device, "cuda")
        with patch.dict(os.environ, {"WHISPER_CPP_DEVICE": "automatic"}, clear=False):
            with self.assertRaisesRegex(ValueError, "WHISPER_CPP_DEVICE"):
                ADAPTER.load_config()

    def test_cuda_runtime_refuses_missing_runtime_or_no_device(self):
        with patch.object(ADAPTER.ctypes, "CDLL", side_effect=OSError("synthetic")):
            with self.assertRaisesRegex(RuntimeError, "CUDA runtime is unavailable"):
                ADAPTER.require_cuda_device()

        def no_devices(pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int)).contents.value = 0
            return 0

        runtime = SimpleNamespace(cudaGetDeviceCount=no_devices)
        with patch.object(ADAPTER.ctypes, "CDLL", return_value=runtime):
            with self.assertRaisesRegex(RuntimeError, "CUDA device is unavailable"):
                ADAPTER.require_cuda_device()

        def one_device(pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int)).contents.value = 1
            return 0

        with patch.object(ADAPTER.ctypes, "CDLL", return_value=SimpleNamespace(cudaGetDeviceCount=one_device)):
            ADAPTER.require_cuda_device()

    def test_cuda_readiness_requires_native_gpu_model_buffer_placement(self):
        cuda = ADAPTER.CandidateConfig(Path("/model"), Path("/key"), device="cuda")
        cpu = ADAPTER.CandidateConfig(Path("/model"), Path("/key"), device="cpu")
        self.assertTrue(ADAPTER.native_health_is_valid(cuda, {
            "status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 1,
        }))
        for fallback in (
            {"status": "ok", "compute_backend": "cpu", "gpu_model_buffer_bytes": 0},
            {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 0},
            {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": True},
            {"status": "ok", "device": "cuda"},
        ):
            with self.subTest(fallback=fallback):
                self.assertFalse(ADAPTER.native_health_is_valid(cuda, fallback))
        self.assertTrue(ADAPTER.native_health_is_valid(cpu, {"status": "ok"}))
        self.assertFalse(ADAPTER.native_health_is_valid(cuda, {"status": "loading model"}))

    def test_cuda_strict_patch_is_pinned_and_source_checksums_match_model_pin(self):
        pin = json.loads((DEPLOY / "model-pin.json").read_text())
        strict = pin["cuda_candidate"]["strict_gpu_patch"]
        self.assertEqual(strict["version"], CUDA_PATCHER.PATCH_VERSION)
        self.assertEqual(strict["patcher_sha256"], hashlib.sha256(
            (DEPLOY / "patch-whisper-cpp-cuda.py").read_bytes()).hexdigest())
        self.assertEqual(strict["patcher_sha256"], LIFECYCLE.CPP_CUDA_PATCHER_SHA256)
        self.assertEqual(strict["patcher_sha256"], ADAPTER.CUDA_PATCHER_SHA256)
        self.assertEqual(strict["version"], ADAPTER.CUDA_PATCH_VERSION)
        self.assertEqual(strict["source_checksums"], {
            path: spec[0] for path, spec in CUDA_PATCHER.PATCHES.items()
        })
        fixture = b"one exact source anchor"
        patched = CUDA_PATCHER.patch_bytes(fixture, hashlib.sha256(fixture).hexdigest(),
                                           "exact source anchor", "changed source anchor")
        self.assertEqual(patched, b"one changed source anchor")
        with self.assertRaisesRegex(ValueError, "checksum changed"):
            CUDA_PATCHER.patch_bytes(fixture + b"x", hashlib.sha256(fixture).hexdigest(),
                                     "exact source anchor", "changed source anchor")

    def test_tracked_pins_and_compose_stay_cpu_private_and_opt_in(self):
        pin = json.loads((DEPLOY / "model-pin.json").read_text())
        compose = (DEPLOY / "compose.yaml").read_text()
        dockerfile = (DEPLOY / "Dockerfile").read_text()
        self.assertEqual(pin["runtime"]["commit"], ADAPTER.RUNTIME_COMMIT)
        self.assertEqual(pin["model"]["sha256"], VERIFY.PIN["sha256"])
        self.assertIn("profiles: [candidate]", compose)
        self.assertIn("platform: linux/amd64", compose)
        self.assertIn("internal: true", compose)
        self.assertIn("logging:\n      driver: none", compose)
        self.assertNotRegex(compose, r"(?m)^\s+ports:")
        self.assertIn("GGML_CUDA=OFF", dockerfile)
        self.assertIn("GGML_METAL=OFF", dockerfile)
        self.assertIn("COPY adapter.py contract.py verify_model.py /opt/homecompute/", dockerfile)
        self.assertIn("HOME_COMPUTE_REVISION: ${WHISPER_CPP_HOME_COMPUTE_REVISION", compose)
        self.assertIn("WHISPER_CPP_IMAGE_ID: ${WHISPER_CPP_IMAGE_ID", compose)
        self.assertIn(ADAPTER.RUNTIME_COMMIT, dockerfile)

    def test_cuda_profile_is_pinned_arm64_opt_in_and_isolated(self):
        pin = json.loads((DEPLOY / "model-pin.json").read_text())
        compose = (DEPLOY / "compose-cuda.yaml").read_text()
        dockerfile = (DEPLOY / "Dockerfile.cuda").read_text()
        cuda = pin["cuda_candidate"]
        self.assertEqual(cuda["platform"], "linux/arm64")
        self.assertEqual(cuda["architecture"], "121a-real")
        self.assertEqual(cuda["cuda_toolkit"], "13.1.115")
        self.assertIn(cuda["base_image"], compose)
        self.assertIn(cuda["base_image"], dockerfile)
        self.assertIn("release 13.1, V13.1.115", dockerfile)
        self.assertIn("cmake version 3.31.6", dockerfile)
        self.assertIn("gcc -dumpfullversion", dockerfile)
        self.assertIn("grep -q 'libggml-cuda'", dockerfile)
        self.assertIn("platform: linux/arm64", compose)
        self.assertIn("profiles: [cuda-candidate]", compose)
        self.assertIn("WHISPER_CPP_DEVICE: cuda", compose)
        self.assertIn("capabilities: [gpu]", compose)
        self.assertIn("internal: true", compose)
        self.assertNotRegex(compose, r"(?m)^\s+ports:")
        self.assertIn("CMAKE_CUDA_ARCHITECTURES=121a-real", dockerfile)
        self.assertIn("-DGGML_CUDA=ON", dockerfile)
        self.assertIn('io.homecompute.whisper-cpp.backend="cuda-121a"', dockerfile)
        self.assertIn('io.homecompute.whisper-cpp.cuda-patch="strict-gpu-buffer-proof-v1"', dockerfile)
        self.assertIn("CUDA_PATCHER_SHA256: ${WHISPER_CPP_CUDA_PATCHER_SHA256:?", compose)
        self.assertIn("gpu_model_buffer_bytes", compose)
        self.assertIn(ADAPTER.CUDA_PATCHER_SHA256, compose)
        self.assertIn("WHISPER_CPP_DEVICE", (DEPLOY / "adapter.py").read_text())

    def test_verbose_native_segments_keep_text_timing_and_exact_char_anchors(self):
        source = {"text": "Hello <|25.52|> world\nAgain.\n", "segments": [
            {"id": 0, "start": 0.0, "end": 0.8, "text": "Hello <|25.52|> world", "words": [{"word": "lie"}]},
            {"id": 1, "start": 0.8, "end": 1.2, "text": "Again.", "words": [{"word": "lie"}],
             "tokens": [1, 2]},
        ]}
        payload, content_type = ADAPTER.normalize_native_response(
            json.dumps(source).encode(), "verbose_json", 2.0)
        result = json.loads(payload)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(result["text"], "Hello  world\nAgain.\n")
        self.assertEqual(result["segments"][0]["text"], "Hello  world")
        self.assertEqual(result["segments"][0]["kind"], "segment")
        self.assertEqual(result["segments"][0]["start_offset"], 0)
        self.assertEqual(result["segments"][0]["end_offset"], len("Hello  world"))
        self.assertEqual(result["segments"][1]["start_offset"], len("Hello  world\n"))
        self.assertEqual(result["segments"][1]["end_offset"], len("Hello  world\nAgain."))
        self.assertNotIn("words", result["segments"][0])
        self.assertEqual(result["segments"][1]["tokens"], [1, 2])

    def test_mismatched_segment_text_does_not_invent_char_anchors(self):
        source = {"text": "canonical text\n", "segments": [{"start": 0, "end": 0.5,
                                                              "text": "different text"}]}
        payload, _ = ADAPTER.normalize_native_response(json.dumps(source).encode(), "verbose_json", 1.0)
        segment = json.loads(payload)["segments"][0]
        self.assertEqual(segment["kind"], "segment")
        self.assertNotIn("start_offset", segment)
        self.assertNotIn("end_offset", segment)

    def test_text_and_plain_json_formats_keep_decoded_text_without_control_tokens(self):
        text, content_type = ADAPTER.normalize_native_response(b"Words <|25.52|> remain\n", "text", 1.0)
        self.assertEqual(text, b"Words  remain\n")
        self.assertEqual(content_type, "text/plain; charset=utf-8")
        payload, content_type = ADAPTER.normalize_native_response(
            b'{"text":"Words <|25.52|> remain","id":"native"}', "json", 1.0)
        self.assertEqual(json.loads(payload), {"text": "Words  remain", "id": "native"})
        self.assertEqual(content_type, "application/json")

    def test_rejects_out_of_bounds_native_time_without_clamping(self):
        source = {"text": "x\n", "segments": [{"start": 0, "end": 2.0, "text": "x"}]}
        with self.assertRaisesRegex(ADAPTER.RequestError, "out-of-bounds"):
            ADAPTER.normalize_native_response(json.dumps(source).encode(), "verbose_json", 1.0)

    def test_real_http_handler_auth_and_forwarding_with_synthetic_native_response(self):
        config = ADAPTER.CandidateConfig(
            Path("/unused/model"), Path("/unused/key"), homecompute_revision="a" * 40,
            image_id="sha256:" + "b" * 64, native_timeout_seconds=2)
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        body, content_type = multipart([
            ("model", "whisper"), ("response_format", "verbose_json"),
            ("language", "auto"), ("prompt", "synthetic neutral\nprompt"),
        ], wav_bytes())
        native = {"text": "Synthetic speech.\n", "segments": [
            {"id": 0, "start": 0.0, "end": 0.1, "text": "Synthetic speech.", "words": []},
        ]}
        observed: dict[str, object] = {}

        def fake_native(forwarded_body: bytes, forwarded_type: str, timeout: int) -> bytes:
            observed["timeout"] = timeout
            parsed = BytesParser(policy=policy.default).parsebytes(
                ("MIME-Version: 1.0\r\nContent-Type: " + forwarded_type + "\r\n\r\n").encode() + forwarded_body)
            observed["fields"] = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True).decode("utf-8")
                                  for p in parsed.iter_parts() if p.get_param("name", header="content-disposition") != "file"}
            observed["audio"] = [p.get_payload(decode=True) for p in parsed.iter_parts()
                                  if p.get_param("name", header="content-disposition") == "file"][0]
            return json.dumps(native).encode()

        with patch.object(ADAPTER, "_native_request", side_effect=fake_native):
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/audio/transcriptions", data=body,
                                             headers={"Content-Type": content_type, "Authorization": "Bearer synthetic-secret-value"})
            with urllib.request.urlopen(request, timeout=3) as response:
                result = json.loads(response.read())
                self.assertEqual(response.headers["X-HomeCompute-STT-Model-SHA256"], VERIFY.PIN["sha256"])
                self.assertEqual(response.headers["X-HomeCompute-STT-Model-Snapshot"], VERIFY.PIN["snapshot"])
                self.assertEqual(response.headers["X-HomeCompute-STT-Runtime"], "whisper.cpp")
                self.assertEqual(response.headers["X-HomeCompute-STT-Runtime-Commit"], ADAPTER.RUNTIME_COMMIT)
                self.assertEqual(response.headers["X-HomeCompute-STT-HomeCompute-Revision"], "a" * 40)
                self.assertEqual(response.headers["X-HomeCompute-STT-Image-ID"], "sha256:" + "b" * 64)
                self.assertEqual(response.headers["X-HomeCompute-STT-Device"], "cpu")
            self.assertEqual(observed["fields"]["prompt"], "synthetic neutral\nprompt")
            self.assertEqual(observed["fields"]["language"], "auto")
            self.assertEqual(observed["fields"]["response_format"], "verbose_json")
            self.assertEqual(observed["fields"]["beam_size"], "5")
            self.assertEqual(observed["fields"]["best_of"], "5")
            self.assertEqual(observed["audio"], wav_bytes())
            self.assertEqual(result["segments"][0]["kind"], "segment")
            self.assertEqual(result["segments"][0]["start_offset"], 0)
            no_auth = urllib.request.Request(f"http://127.0.0.1:{port}/v1/audio/transcriptions", data=body,
                                             headers={"Content-Type": content_type})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(no_auth, timeout=3)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    def test_health_reports_device_provenance_and_inference_busy_state(self):
        config = ADAPTER.CandidateConfig(
            Path("/unused/model"), Path("/unused/key"), homecompute_revision="a" * 40,
            image_id="sha256:" + "b" * 64, device="cuda")
        native_health = {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 4096}
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0),
                                         native_health=native_health)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/health"
            with urllib.request.urlopen(url, timeout=3) as response:
                healthy = json.loads(response.read())
            self.assertEqual(healthy["device"], "cuda")
            self.assertEqual(healthy["compute_backend"], "cuda")
            self.assertEqual(healthy["gpu_model_buffer_bytes"], 4096)
            self.assertEqual(healthy["strict_gpu_patch"], ADAPTER.CUDA_PATCH_VERSION)
            self.assertEqual(healthy["strict_gpu_patcher_sha256"], ADAPTER.CUDA_PATCHER_SHA256)
            self.assertEqual(healthy["inference_busy"], False)
            self.assertTrue(server.inflight.acquire(blocking=False))
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    busy = json.loads(response.read())
                self.assertEqual(busy["inference_busy"], True)
            finally:
                server.inflight.release()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_cuda_success_response_identifies_backend_without_changing_text(self):
        config = ADAPTER.CandidateConfig(
            Path("/unused/model"), Path("/unused/key"), homecompute_revision="a" * 40,
            image_id="sha256:" + "b" * 64, device="cuda")
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0),
                                         native_health={"status": "ok", "compute_backend": "cuda",
                                                        "gpu_model_buffer_bytes": 4096})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body, content_type = multipart([("model", "whisper"), ("response_format", "json")], wav_bytes())
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions"
            request = urllib.request.Request(url, data=body, headers={
                "Content-Type": content_type, "Authorization": "Bearer synthetic-secret-value"})
            with patch.object(ADAPTER, "_native_request", return_value=b'{"text":"Synthetic words"}'):
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.headers["X-HomeCompute-STT-Device"], "cuda")
                    self.assertEqual(json.loads(response.read()), {"text": "Synthetic words"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
