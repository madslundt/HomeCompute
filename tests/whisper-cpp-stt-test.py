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
import re
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
CUDA_LINKAGE = load_file("whisper_cuda_linkage", DEPLOY / "verify-cuda-linkage.py")
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


class IntakeGateListingTests(unittest.TestCase):
    @staticmethod
    def result(code=0, stdout="", stderr=""):
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def test_absence_uses_successful_table_listing_across_backend_messages(self):
        chain = "HC-STT-0123456789ab"
        listing = "-P INPUT ACCEPT\n-P OUTPUT ACCEPT\n-P FORWARD DROP\n"
        for failure in (
            self.result(1, stderr="iptables: No chain/target/match by that name."),
            self.result(1, stdout="iptables v1.8.10 (nf_tables): Chain does not exist."),
            self.result(1, stderr="iptables: cible/chaîne introuvable"),
        ):
            with self.subTest(failure=failure):
                with patch.object(LIFECYCLE, "run", return_value=self.result(stdout=listing)):
                    self.assertFalse(LIFECYCLE.chain_exists(failure, chain))

    def test_absence_requires_successful_well_formed_listing_and_detects_race(self):
        chain = "HC-STT-0123456789ab"
        failed = self.result(1, stderr="denied")
        with patch.object(LIFECYCLE, "run", return_value=self.result(1, stderr="permission denied")):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                LIFECYCLE.chain_exists(failed, chain)
        with patch.object(LIFECYCLE, "run", return_value=self.result(stdout="garbage listing\n")):
            with self.assertRaisesRegex(ValueError, "malformed"):
                LIFECYCLE.chain_exists(failed, chain)
        race = self.result(stdout=f"-P INPUT ACCEPT\n-P OUTPUT ACCEPT\n-P FORWARD DROP\n-N {chain}\n")
        with patch.object(LIFECYCLE, "run", return_value=race):
            with self.assertRaisesRegex(ValueError, "changed during inspection"):
                LIFECYCLE.chain_exists(failed, chain)

    def test_jump_absence_uses_authoritative_output_listing_and_rejects_races(self):
        chain = "HC-STT-0123456789ab"
        missing = self.result(1, stdout="iptables: bad rule", stderr="localized text")
        with patch.object(LIFECYCLE, "run", return_value=self.result(
                stdout="-P OUTPUT ACCEPT\n-A OUTPUT -j DOCKER\n")):
            self.assertFalse(LIFECYCLE.jump_exists(missing, chain))
        with patch.object(LIFECYCLE, "run", return_value=self.result(
                stdout=f"-P OUTPUT ACCEPT\n-A OUTPUT -j {chain}\n")):
            with self.assertRaisesRegex(ValueError, "changed during inspection"):
                LIFECYCLE.jump_exists(missing, chain)
        with patch.object(LIFECYCLE, "run", return_value=self.result(1, stderr="permission denied")):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                LIFECYCLE.jump_exists(missing, chain)

    def test_malformed_and_duplicate_output_jumps_fail_closed(self):
        chain = "HC-STT-0123456789ab"
        with patch.object(LIFECYCLE, "run", return_value=self.result(stdout="-P OUTPUT ACCEPT\ninvalid\n")):
            with self.assertRaisesRegex(ValueError, "malformed"):
                LIFECYCLE.output_jump_present(chain)
        duplicate = f"-P OUTPUT ACCEPT\n-A OUTPUT -j {chain}\n-A OUTPUT -j {chain}\n"
        with patch.object(LIFECYCLE, "run", return_value=self.result(stdout=duplicate)):
            with self.assertRaisesRegex(ValueError, "jump drift"):
                LIFECYCLE.output_jump_present(chain)

    def test_successful_but_empty_or_incomplete_listings_are_not_absence_proof(self):
        chain = "HC-STT-0123456789ab"
        failed = self.result(1, stdout="backend-specific missing chain")
        with patch.object(LIFECYCLE, "run", return_value=self.result()):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                LIFECYCLE.chain_exists(failed, chain)
        with patch.object(LIFECYCLE, "run", return_value=self.result(stdout="-A OUTPUT -j DOCKER\n")):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                LIFECYCLE.output_jump_present(chain)

    def test_conditional_jump_to_nonce_is_drift_not_an_owned_gate(self):
        chain = "HC-STT-0123456789ab"
        output = self.result(stdout=f"-P OUTPUT ACCEPT\n-A OUTPUT -s 127.0.0.1 -j {chain}\n")
        with patch.object(LIFECYCLE, "run", return_value=output):
            with self.assertRaisesRegex(ValueError, "jump drift"):
                LIFECYCLE.output_jump_present(chain)

    def test_gate_cleanup_of_absent_nonce_is_verified_without_mutation(self):
        chain = "HC-STT-0123456789ab"
        absent_chain = self.result(1, stdout="nft: unknown chain")
        absent_jump = self.result(1, stderr="translated missing-rule diagnostic")
        table = self.result(stdout="-P INPUT ACCEPT\n-P OUTPUT ACCEPT\n-P FORWARD DROP\n")
        output = self.result(stdout="-P OUTPUT ACCEPT\n-A OUTPUT -j DOCKER\n")
        with patch.object(LIFECYCLE, "run", side_effect=[absent_chain, table, absent_jump, output]) as run:
            LIFECYCLE.gate(chain, False)
        self.assertEqual(run.call_count, 4)

    def test_gate_cleanup_accepts_only_owned_partial_nonce_rules(self):
        chain = "HC-STT-0123456789ab"
        partial = self.result(stdout=(f"-N {chain}\n-A {chain} -p tcp -m conntrack --ctstate NEW "
                                     "--ctorigdst 127.0.0.1 --ctorigdstport 8003 -j REJECT\n"))
        missing_jump = self.result(1, stderr="nft backend diagnostic")
        output = self.result(stdout="-P OUTPUT ACCEPT\n")
        completed = self.result()
        with patch.object(LIFECYCLE, "run", side_effect=[partial, missing_jump, output, output,
                                                          completed, completed]) as run:
            LIFECYCLE.gate(chain, False)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["iptables", "-w", "-F", chain], commands)
        self.assertIn(["iptables", "-w", "-X", chain], commands)

    def test_recovery_is_restricted_to_healthy_pretransition_legacy_failure(self):
        chain = "HC-STT-0123456789ab"
        revision = "a" * 40
        primary = {
            "Id": "primary-id", "Image": "primary-image", "Name": "/gb10-whisper-primary",
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {"Image": "nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d",
                       "Cmd": [LIFECYCLE.MODEL, LIFECYCLE.REVISION]},
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0}},
        }
        relay = {
            "Id": "relay-id", "Image": LIFECYCLE.CPP_GATE_FAILURE_RELAY_IMAGE, "Name": "/gb10-whisper-http",
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {"Image": "homecompute/stt-http:2.0.0", "Labels": {
                "com.docker.compose.project": "whisper-large-v3-stt",
                "com.docker.compose.service": "whisper-http"}},
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0}},
        }
        tx = {"revision": LIFECYCLE.CPP_GATE_FAILURE_RECOVERY_REVISION,
              "state": "rollback-pending", "promotion": True,
              "candidate_stage": "install-intake-gate", "failure_stage": "install-intake-gate",
              "rollback_stage": None, "candidate_exposed": False, "chain": chain,
              "candidate_image_id": LIFECYCLE.CPP_QUALIFIED_IMAGE_ID,
              "image_source_revision": LIFECYCLE.CPP_QUALIFIED_SOURCE_REVISION,
              "primary_id": primary["Id"], "primary_image": primary["Image"],
              "primary_restart": "unless-stopped", "failure_id": LIFECYCLE.CPP_GATE_FAILURE_RECOVERY_ID}
        saved = []
        with (patch.object(LIFECYCLE, "STATE", Path("/unused")),
              patch.object(LIFECYCLE, "protected"),
              patch.object(LIFECYCLE, "inspect", side_effect=lambda name: {
                  "primary-id": primary, "gb10-whisper-http": relay,
                  LIFECYCLE.CUDA_CANDIDATE: None, "gb10-whisper-cpp-http": None}.get(name)),
              patch.object(LIFECYCLE, "gate") as gate,
              patch.object(LIFECYCLE, "gate_is_active", return_value=False),
              patch.object(LIFECYCLE, "save_cuda_candidate", side_effect=lambda value: saved.append(dict(value)))):
            with tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / "cuda-candidate.json"
                journal.write_text(json.dumps(tx))
                with patch.object(LIFECYCLE, "STATE", Path(tmp)):
                    LIFECYCLE.recover_pretransition_promotion_failure(revision)
        gate.assert_called_once_with(chain, False)
        self.assertEqual(saved[-1]["state"], "rolled-back")
        self.assertEqual(saved[-1]["failure_id"], LIFECYCLE.CPP_GATE_FAILURE_RECOVERY_ID)
        self.assertTrue(saved[-1]["pretransition_gate_recovered"])

        completed = dict(tx, state="rolled-back", rollback_stage="complete", rollback_status="completed")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "cuda-candidate.json").write_text(json.dumps(completed))
            with (patch.object(LIFECYCLE, "STATE", Path(tmp)), patch.object(LIFECYCLE, "protected"),
                  patch.object(LIFECYCLE, "inspect", side_effect=lambda name: {
                      "primary-id": primary, "gb10-whisper-http": relay,
                      LIFECYCLE.CUDA_CANDIDATE: None, "gb10-whisper-cpp-http": None}.get(name)),
                  patch.object(LIFECYCLE, "gate") as gate,
                  patch.object(LIFECYCLE, "gate_is_active", return_value=False),
                  patch.object(LIFECYCLE, "save_cuda_candidate") as save):
                LIFECYCLE.recover_pretransition_promotion_failure(revision)
                gate.assert_called_once_with(chain, False)
                self.assertEqual(save.call_args.args[0]["state"], "rolled-back")

        for unsafe in (dict(tx, candidate_stage="recording-relay-update"),
                       dict(tx, revision="c" * 40), dict(tx, failure_id="aaaaaaaaaaaa")):
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "cuda-candidate.json").write_text(json.dumps(unsafe))
                with (patch.object(LIFECYCLE, "STATE", Path(tmp)), patch.object(LIFECYCLE, "protected"),
                      patch.object(LIFECYCLE, "inspect", side_effect=lambda name: {
                          "primary-id": primary, "gb10-whisper-http": relay,
                          LIFECYCLE.CUDA_CANDIDATE: None, "gb10-whisper-cpp-http": None}.get(name)),
                      patch.object(LIFECYCLE, "gate") as gate):
                    with self.assertRaisesRegex(ValueError, "not a recoverable"):
                        LIFECYCLE.recover_pretransition_promotion_failure(revision)
                    gate.assert_not_called()

    def test_public_failure_metadata_uses_final_rollback_and_gate_receipt(self):
        receipt = {
            "failure_id": "0123456789ab", "failure_stage": "install-intake-gate",
            "failure_class": "ValueError", "failure_executable": None,
            "failure_exit_code": None, "failure_classification": "unknown",
            "failure_diagnostic_available": True, "rollback_status": "completed",
            "rollback_failure_stage": None, "rollback_failure_class": None,
            "rollback_failure_classification": "unknown", "rollback_failure_executable": None,
            "rollback_failure_exit_code": None, "gate_cleanup_failure_class": None,
        }
        self.assertEqual(LIFECYCLE.public_cuda_failure_metadata(receipt), receipt)
        message = LIFECYCLE.format_cuda_candidate_failure(
            LIFECYCLE.public_cuda_failure_metadata(receipt))
        self.assertIn("stage=install-intake-gate", message)
        self.assertIn("rollback=completed", message)

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
    def test_cuda_build_linkage_accepts_only_host_injected_driver_missing(self):
        expected_host_driver_gap = """\
        linux-vdso.so.1 (0x0000ffff)
        libwhisper.so.1 => /usr/local/lib/libwhisper.so.1 (0x0000ffff)
        libggml-cuda.so.0 => /usr/local/lib/libggml-cuda.so.0 (0x0000ffff)
        libcuda.so.1 => not found
        libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x0000ffff)
        """
        valid, _message = CUDA_LINKAGE.validate_ldd_output(expected_host_driver_gap)
        self.assertTrue(valid)

    def test_cuda_linkage_rejects_unexpected_missing_or_unlinked_backend(self):
        resolved_cuda = "libggml-cuda.so.0 => /usr/local/lib/libggml-cuda.so.0 (0x0000ffff)\n"
        for output in (
            resolved_cuda + "libcublas.so.13 => not found\n",
            "libcuda.so.1 => not found\n",
            "libggml-cuda.so.0 => not found\n",
        ):
            with self.subTest(output=output):
                valid, _message = CUDA_LINKAGE.validate_ldd_output(output)
                self.assertFalse(valid)

    def test_cuda_sdk_stub_probe_detects_build_host_driver_boundary(self):
        missing_host_driver = """\
        linux-vdso.so.1 (0x0000ffff)
        libcuda.so.1 => not found
        libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x0000ffff)
        /lib/ld-linux-aarch64.so.1 (0x0000ffff)
        """
        valid, _message = CUDA_LINKAGE.validate_driver_probe(missing_host_driver)
        self.assertTrue(valid)
        for wrong in (
            missing_host_driver.replace("libcuda.so.1 => not found", "libcuda.so.1 => /usr/local/cuda/lib64/stubs/libcuda.so.1"),
            missing_host_driver.replace("libcuda.so.1 => not found", "libcuda.so.1 => not found\nlibcublas.so.13 => not found"),
            missing_host_driver.replace("libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x0000ffff)", "libc.so.6 => not found"),
        ):
            with self.subTest(wrong=wrong):
                valid, _message = CUDA_LINKAGE.validate_driver_probe(wrong)
                self.assertFalse(valid)

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
        self.assertIn(("max_len", "-1"), forwarded)
        _, defaults = ADAPTER.normalize_fields({"model": "whisper"})
        self.assertIn(("beam_size", "5"), defaults)
        self.assertIn(("best_of", "5"), defaults)
        self.assertIn(("max_len", "-1"), defaults)
        _, greedy = ADAPTER.normalize_fields({"model": "whisper", "beam_size": "-1", "best_of": "1"})
        self.assertIn(("beam_size", "-1"), greedy)
        self.assertIn(("best_of", "1"), greedy)
        _, unwrapped = ADAPTER.normalize_fields({"model": "whisper", "max_len": "-1"})
        self.assertIn(("max_len", "-1"), unwrapped)
        _, bounded_wrap = ADAPTER.normalize_fields({"model": "whisper", "max_len": "80"})
        self.assertIn(("max_len", "80"), bounded_wrap)

    def test_rejects_unsupported_fields_and_word_timestamps(self):
        body, content_type = multipart([("model", "whisper"), ("initial_prompt", "unmapped")], wav_bytes())
        with self.assertRaisesRegex(ADAPTER.RequestError, "Unsupported"):
            ADAPTER.parse_multipart(body, content_type)
        with self.assertRaisesRegex(ADAPTER.RequestError, "Word timestamps are unsupported"):
            ADAPTER.normalize_fields({"model": "whisper", "response_format": "verbose_json",
                                      "timestamp_granularities[]": "word"})
        with self.assertRaisesRegex(ADAPTER.RequestError, "Only text, json"):
            ADAPTER.normalize_fields({"model": "whisper", "response_format": "srt"})
        for name, value in (("beam_size", "0"), ("beam_size", "17"), ("best_of", "0"),
                            ("best_of", "17"), ("max_len", "0"), ("max_len", "1001"),
                            ("max_len", "-2")):
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
            with self.assertRaisesRegex(RuntimeError, "CUDA driver library is unavailable"):
                ADAPTER.require_cuda_device()

        with patch.object(ADAPTER.ctypes, "CDLL", side_effect=[object(), OSError("synthetic")]):
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
            "inference_busy": False,
        }))
        for fallback in (
            {"status": "ok", "compute_backend": "cpu", "gpu_model_buffer_bytes": 0},
            {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 0},
            {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": True},
            {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 1,
             "inference_busy": None},
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
        self.assertIn("!ggml_backend_buffer_is_host(buffer)", CUDA_PATCHER.IMPLEMENTATION)
        self.assertIn("std::try_to_lock", CUDA_PATCHER.PATCHES["examples/server/server.cpp"][2])
        self.assertIn('health["gpu_model_buffer_bytes"] = gpu_model_buffer_bytes;',
                      CUDA_PATCHER.PATCHES["examples/server/server.cpp"][2])
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

    def test_native_http_max_len_semantics_are_build_guarded_for_cpu_and_cuda(self):
        expected = (
            'params.max_len = std::stoi(req.get_file_value("max_len").content);',
            'wparams.max_len          = params.max_len == 0 ? 60 : params.max_len;',
            'if (params.max_len > 0) {',
        )
        for filename in ("Dockerfile", "Dockerfile.cuda"):
            source = (DEPLOY / filename).read_text()
            for semantic in expected:
                with self.subTest(filename=filename, semantic=semantic):
                    self.assertIn("grep -Fq '" + semantic + "'", source)
        _, defaults = ADAPTER.normalize_fields({"model": "whisper"})
        self.assertEqual(dict(defaults)["max_len"], "-1")

    def test_cuda_model_provisioning_url_and_bytes_match_tracked_model_pin(self):
        pin = json.loads((DEPLOY / "model-pin.json").read_text())["model"]
        self.assertEqual(pin["download_url"], LIFECYCLE.CPP_MODEL_URL)
        self.assertEqual(pin["sha256"], LIFECYCLE.CPP_MODEL_SHA256)
        self.assertEqual(pin["size_bytes"], LIFECYCLE.CPP_MODEL_SIZE)

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
        self.assertIn("verify-cuda-linkage.py", dockerfile)
        self.assertIn("platform: linux/arm64", compose)
        self.assertIn("profiles: [cuda-candidate]", compose)
        self.assertIn("WHISPER_CPP_DEVICE: cuda", compose)
        self.assertIn("capabilities: [gpu]", compose)
        self.assertIn("internal: true", compose)
        self.assertNotRegex(compose, r"(?m)^\s+ports:")
        self.assertIn("CMAKE_CUDA_ARCHITECTURES=121a-real", dockerfile)
        self.assertIn("-DGGML_CUDA=ON", dockerfile)
        self.assertIn('io.homecompute.whisper-cpp.backend="cuda-121a"', dockerfile)
        self.assertIn('io.homecompute.whisper-cpp.cuda-patch="strict-gpu-buffer-idle-proof-v2"', dockerfile)
        self.assertIn("CUDA_PATCHER_SHA256: ${WHISPER_CPP_CUDA_PATCHER_SHA256:?", compose)
        self.assertIn("gpu_model_buffer_bytes", compose)
        self.assertIn("NVIDIA_DRIVER_CAPABILITIES: compute,utility", compose)
        self.assertIn("LD_LIBRARY_PATH=/usr/local/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64", dockerfile)
        self.assertIn("verify-cuda-linkage.py", dockerfile)
        self.assertIn("/tmp/whisper-server.ldd", dockerfile)
        self.assertIn(ADAPTER.CUDA_PATCHER_SHA256, compose)
        self.assertIn("native_inference_busy", compose)
        self.assertIn("WHISPER_CPP_DEVICE", (DEPLOY / "adapter.py").read_text())

    def test_cuda_candidate_joins_only_private_inference_network_with_stable_whisper_aliases(self):
        compose = (DEPLOY / "compose-cuda-production.yaml").read_text()
        self.assertIn("aliases: [whisper-primary, whisper-cpp-recording]", compose)
        self.assertIn("name: whisper-large-v3-stt_inference", compose)
        self.assertIn("inference:\n    external: true", compose)
        self.assertNotRegex(compose, r"(?m)^\s+ports:")
        qualification = (DEPLOY / "compose-cuda.yaml").read_text()
        self.assertIn("container_name: gb10-whisper-cpp-primary", qualification)
        self.assertIn("networks: [candidate]", qualification)
        self.assertNotIn("whisper-primary", qualification)

    def test_promoted_relay_is_opt_in_bounded_and_leaves_normal_relay_unchanged(self):
        compose = (ROOT / "deploy/whisper-stt/compose.yaml").read_text()
        normal = re.search(r"(?ms)^  whisper-http:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)", compose).group(1)
        promoted = re.search(r"(?ms)^  whisper-cpp-http:\n(.*?)(?=^networks:|\Z)", compose).group(1)
        self.assertIn("mem_limit: 256m", normal)
        self.assertIn('STT_MAX_UPLOAD_BYTES: "10000000"', normal)
        self.assertIn('STT_MAX_AUDIO_SECONDS: "60"', normal)
        self.assertIn("profiles: [cpp-production]", promoted)
        self.assertIn("container_name: gb10-whisper-cpp-http", promoted)
        self.assertIn('restart: "no"', promoted)
        self.assertIn("mem_limit: 512m", promoted)
        self.assertIn('STT_RECORDING_MAX_UPLOAD_BYTES: "40000000"', promoted)
        self.assertIn('STT_RECORDING_MAX_AUDIO_SECONDS: "1200"', promoted)
        self.assertIn('STT_RECORDING_UPSTREAM_TIMEOUT_SECONDS: "1800"', promoted)
        self.assertIn('127.0.0.1:8007:8007', promoted)
        self.assertIn('127.0.0.1:8003:8007', promoted)

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

    def test_one_second_audio_rejects_29_98_second_native_segment_without_clamping(self):
        source = {"text": "synthetic", "segments": [{"start": 0.0, "end": 29.98, "text": "synthetic"}]}
        with self.assertRaisesRegex(ADAPTER.RequestError, "out-of-bounds") as caught:
            ADAPTER.normalize_native_response(json.dumps(source).encode(), "verbose_json", 1.0)
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(caught.exception.message, "Speech backend returned out-of-bounds segment timestamps")

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
            self.assertEqual(observed["fields"]["max_len"], "-1")
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
        native_health = {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 4096,
                         "inference_busy": False}
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0),
                                         native_health=native_health)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/health"
            with patch.object(ADAPTER, "_native_health", return_value=native_health):
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
                with patch.object(ADAPTER, "_native_health", return_value=native_health):
                    with urllib.request.urlopen(url, timeout=3) as response:
                        busy = json.loads(response.read())
                self.assertEqual(busy["inference_busy"], True)
            finally:
                server.inflight.release()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_native_busy_after_adapter_timeout_stays_busy_and_unknown_is_not_idle(self):
        config = ADAPTER.CandidateConfig(
            Path("/unused/model"), Path("/unused/key"), homecompute_revision="a" * 40,
            image_id="sha256:" + "b" * 64, device="cuda")
        startup = {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 4096,
                   "inference_busy": False}
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0),
                                         native_health=startup)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/health"
        try:
            with patch.object(ADAPTER, "_native_health", return_value={"status": "ok", "inference_busy": True}):
                with urllib.request.urlopen(url, timeout=3) as response:
                    busy = json.loads(response.read())
                self.assertEqual(busy["status"], "ok")
                self.assertEqual(busy["native_inference_busy"], True)
                self.assertEqual(busy["inference_busy"], True)
                self.assertEqual(busy["gpu_model_buffer_bytes"], 4096)
            with patch.object(ADAPTER, "_native_health", return_value=None):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url, timeout=3)
                unknown = json.loads(error.exception.read())
                error.exception.close()
                self.assertEqual(error.exception.code, 503)
                self.assertEqual(unknown["status"], "unavailable")
                self.assertEqual(unknown["inference_busy"], True)
            for current, expected_native_busy, expected_busy in (
                ({"status": "ok"}, None, True),
                ({"status": "ok", "inference_busy": "false"}, "false", True),
                ({"status": "ok", "inference_busy": False}, False, False),
            ):
                with self.subTest(current=current), patch.object(ADAPTER, "_native_health", return_value=current):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(url, timeout=3)
                    payload = json.loads(error.exception.read())
                    error.exception.close()
                    self.assertEqual(error.exception.code, 503)
                    self.assertEqual(payload["native_inference_busy"], expected_native_busy)
                    self.assertEqual(payload["inference_busy"], expected_busy)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_cuda_post_refuses_native_busy_or_unknown_after_adapter_slot_is_free(self):
        config = ADAPTER.CandidateConfig(
            Path("/unused/model"), Path("/unused/key"), homecompute_revision="a" * 40,
            image_id="sha256:" + "b" * 64, device="cuda")
        native_health = {"status": "ok", "compute_backend": "cuda", "gpu_model_buffer_bytes": 4096,
                         "inference_busy": False}
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0),
                                         native_health=native_health)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body, content_type = multipart([("model", "whisper"), ("response_format", "json")], wav_bytes())
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions"
        try:
            for current, expected in (({"status": "ok", "inference_busy": True}, 429), (None, 503)):
                request = urllib.request.Request(url, data=body, headers={
                    "Content-Type": content_type, "Authorization": "Bearer synthetic-secret-value"})
                with patch.object(ADAPTER, "_native_health", return_value=current), \
                     patch.object(ADAPTER, "_native_request") as native_request:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(error.exception.code, expected)
                    error.exception.close()
                    native_request.assert_not_called()
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
                                                        "gpu_model_buffer_bytes": 4096,
                                                        "inference_busy": False})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body, content_type = multipart([("model", "whisper"), ("response_format", "json")], wav_bytes())
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions"
            request = urllib.request.Request(url, data=body, headers={
                "Content-Type": content_type, "Authorization": "Bearer synthetic-secret-value"})
            with patch.object(ADAPTER, "_native_health", return_value=server.native_health), \
                 patch.object(ADAPTER, "_native_request", return_value=b'{"text":"Synthetic words"}'):
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.headers["X-HomeCompute-STT-Device"], "cuda")
                    self.assertEqual(json.loads(response.read()), {"text": "Synthetic words"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
