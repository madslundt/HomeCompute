"""Isolated, source-pinned OpenAI Whisper engine using the approved GGML values."""
from __future__ import annotations

import base64
from collections import Counter
import hashlib
import importlib.metadata
import math
from pathlib import Path
import struct
import threading
from typing import Callable

from ggml_loader import (FILTER_SHA256, MODEL_SHA256, VOCAB_SHA256, VERSION,
                         load_approved_model)

REFERENCE_COMMIT = "31243bad24cc746f07d4c8bfdd2d974872cb1803"
MODEL_SNAPSHOT = "c521a4b02f422512d734391fdf08bb08c0862f68"
REFERENCE_FILES = {
    "__init__.py": "79ecbed714783b6230931a3dba6d5da6d386e2d44f9fc27256e4fc72751e1f62",
    "model.py": "473491a97554ad1d0beaaef6fe91d965f12785b466b93ebb61024307b50ad683",
    "transcribe.py": "848b82877500d6fb8f0b91bee7a08ac67ef670a113591d11b494848d263ad0f6",
    "decoding.py": "94084fedc5af74cabf8e1883a85552700057081f00ea669ad49435ecb29039db",
    "audio.py": "36ed287b2de3eaf40207cd2a44a8d45d57ea5c212a39df07401d4fcb4d5a5a1a",
    "tokenizer.py": "3b48e361a7e95b4ec0356ca6d72bba635778aa10269153136ee7bc34cae30b85",
    "utils.py": "c25990446ab7dd5ba30bd52bd4f0e6537f11b5e361a015f02c21355d41027b05",
    "timing.py": "bb091c973c428697012f0f8d1b4f5f3a605cd93c5d1c129957f712a6a878a59c",
    "assets/multilingual.tiktoken": "b34b360dbb493e781e479794586d661700670d65564001f23024971d1f2fa126",
    "assets/mel_filters.npz": "7450ae70723a5ef9d341e3cee628c7cb0177f36ce42c44b7ed2bf3325f0f6d4c",
}
# Deliberately fixed matched-T0 arm, not the package's multi-temperature defaults.
# gzip compression_ratio is NOT whisper.cpp's entropy test; no fallback can retry here.
DECODING_OPTIONS = {
    "temperature": (0.0,), "beam_size": 5, "best_of": 5,
    "condition_on_previous_text": False, "initial_prompt": None,
    "carry_initial_prompt": False, "task": "transcribe", "word_timestamps": False,
    # Resolved again against the canonical tokenizer at load. This excludes -1's
    # broad ordinary nonspeech-token set while retaining native mandatory masks.
    "suppress_tokens": list(range(50258, 50365)),
    "suppress_blank": True, "max_initial_timestamp": 1.0,
    "compression_ratio_threshold": 2.4, "logprob_threshold": -1.0,
    "no_speech_threshold": 0.6, "fp16": True, "verbose": None,
}
Guard = Callable[[], None]
MAX_DECODE_OBSERVATIONS = 64


class RuntimeGuardError(RuntimeError):
    pass


def resolve_suppression_tokens(tokenizer, language_codes) -> list[int]:
    codes = set(language_codes)
    languages = {tokenizer.to_language_token(code) for code in codes}
    if len(codes) != 100 or len(languages) != 100 or languages != set(tokenizer.all_language_tokens):
        raise RuntimeError("Canonical language token inventory mismatch")
    mandatory = {getattr(tokenizer, key) for key in (
        "sot", "sot_lm", "sot_prev", "transcribe", "translate", "no_speech", "no_timestamps")}
    resolved = sorted(languages | mandatory)
    if resolved != DECODING_OPTIONS["suppress_tokens"]:
        raise RuntimeError("Canonical mandatory token mask mismatch")
    return resolved


def observed_model_class(reference):
    """Observe canonical decode results before transcribe trims segment tokens."""
    class ObservedWhisper(reference.Whisper):
        def decode(self, mel, options=None, **kwargs):
            if len(self.decoding_observations) >= MAX_DECODE_OBSERVATIONS:
                raise RuntimeError("Reference decoding observation limit exceeded")
            # Delegate without changing options, tokens, rank, filters or return object.
            if options is None:
                result = super().decode(mel, **kwargs)
            else:
                result = super().decode(mel, options, **kwargs)
            item = result
            if isinstance(result, list):
                if len(result) != 1:
                    raise RuntimeError("Reference shadow requires one decoded item")
                item = result[0]
            budget = kwargs.get("sample_len", getattr(options, "sample_len", None))
            budget = self.dims.n_text_ctx // 2 if budget is None else budget
            temperature = kwargs.get("temperature", getattr(options, "temperature", 0.0))
            if not isinstance(budget, int) or budget <= 0 or \
                    not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
                raise RuntimeError("Invalid reference decoding observation")
            count = len(item.tokens)
            self.decoding_observations.append({"temperature": temperature, "token_count": count,
                                              "effective_sample_len": budget, "budget_reached": count >= budget})
            return result
    return ObservedWhisper


def verify_reference_assets(package: Path, np) -> None:
    for name, expected in REFERENCE_FILES.items():
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Pinned reference source or asset mismatch")
    # Match GGML's ordered base vocabulary, not its larger special-token n_vocab.
    ranks = {}
    for line in (package / "assets/multilingual.tiktoken").read_bytes().splitlines():
        token, rank = line.split()
        ranks[int(rank)] = base64.b64decode(token)
    if set(ranks) != set(range(50257)):
        raise RuntimeError("Reference base tokenizer rank mismatch")
    digest = hashlib.sha256(struct.pack("<i", len(ranks)))
    for index in range(len(ranks)):
        value = ranks[index]
        digest.update(struct.pack("<i", len(value)))
        digest.update(value)
    if digest.hexdigest() != VOCAB_SHA256:
        raise RuntimeError("Reference tokenizer differs from GGML")
    with np.load(package / "assets/mel_filters.npz", allow_pickle=False) as arrays:
        filters = arrays["mel_128"]
        if filters.shape != (128, 201) or filters.dtype != np.dtype("float32") or \
                hashlib.sha256(filters.tobytes()).hexdigest() != FILTER_SHA256:
            raise RuntimeError("Reference mel filters differ from GGML")


class ReferenceEngine:
    def __init__(self, model_path: str | Path, device: str = "cuda:0", *, guard: Guard | None = None):
        if device != "cuda:0":
            raise ValueError("Reference engine requires cuda:0; CPU fallback is disabled")
        self.model_path = Path(model_path)
        self.device = device
        self.guard = guard
        self.model = None
        self.torch = None
        self.np = None
        self._active_guard = None
        self._hooks = []
        self._description = None
        self._request_lock = threading.Lock()
        self._suppression_tokens = None

    def _check(self) -> None:
        if self.guard is not None:
            self.guard()
        if self._active_guard is not None:
            self._active_guard()

    def load(self) -> None:
        if self.model is not None:
            raise RuntimeError("Reference engine is already loaded")
        self._check()
        # Deferred imports permit stdlib-only adapter and contract tests.
        import numpy as np
        import torch
        import whisper
        from whisper import model as reference
        from whisper.tokenizer import LANGUAGES, get_tokenizer

        verify_reference_assets(Path(whisper.__file__).parent, np)
        self._suppression_tokens = resolve_suppression_tokens(
            get_tokenizer(multilingual=True, num_languages=100, language="en", task="transcribe"), LANGUAGES)
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError("Exactly one visible CUDA device is required")
        torch.use_deterministic_algorithms(True)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        self.torch, self.np = torch, np
        try:
            self.model = load_approved_model(self.model_path, reference, torch,
                                              device=self.device, guard=self._check,
                                              model_class=observed_model_class(reference))
            self.model.decoding_observations = []
            if not self.model.is_multilingual or self.model.num_languages != 100:
                raise RuntimeError("Reference language vocabulary mismatch")
            for module in (self.model.encoder, self.model.decoder):
                self._hooks.append(module.register_forward_pre_hook(lambda _m, _a: self._check()))
                self._hooks.append(module.register_forward_hook(lambda _m, _a, _o: self._check()))
            self._description = self._resident_description()
            self._check()
        except BaseException:
            self.close()
            raise

    def _resident_description(self) -> dict:
        from whisper.tokenizer import LANGUAGES
        state = self.model.state_dict()
        if any(str(t.device) != self.device or t.is_meta for t in state.values()):
            raise RuntimeError("Persistent reference state must be wholly CUDA resident")
        counts = Counter(str(t.dtype).removeprefix("torch.") for t in state.values())
        dtype_bytes = Counter()
        for tensor in state.values():
            dtype_bytes[str(tensor.dtype).removeprefix("torch.")] += tensor.numel() * tensor.element_size()
        if dict(counts) != {"float32": 744, "float16": 515} or sum(dtype_bytes.values()) != 3094359040:
            raise RuntimeError("Resident reference tensor inventory mismatch")
        return {
            "runtime": "openai-whisper-reference", "runtime_commit": REFERENCE_COMMIT,
            "model_sha256": MODEL_SHA256, "model_snapshot": MODEL_SNAPSHOT,
            "model_format": "approved-unquantized-ggml-f16-f32", "device": self.device,
            "language_codes": sorted(LANGUAGES),
            "loader_version": VERSION,
            "loader_sha256": hashlib.sha256(Path(__file__).with_name("ggml_loader.py").read_bytes()).hexdigest(),
            "persistent_tensor_count": len(state), "persistent_tensor_bytes": sum(dtype_bytes.values()),
            "persistent_dtype_counts": dict(counts), "persistent_dtype_bytes": dict(dtype_bytes),
            "parameter_bytes": sum(p.numel() * p.element_size() for p in self.model.parameters()),
            "nonpersistent_buffers": {"decoder.mask": str(self.model.decoder.mask.device),
                                      "alignment_heads": str(self.model.alignment_heads.device)},
            "alignment_policy": "canonical default heads; unused because word timestamps are disabled",
            "torch_version": self.torch.__version__, "cuda_version": self.torch.version.cuda,
            "whisper_package_version": importlib.metadata.version("openai-whisper"),
            "decoding_options": {**DECODING_OPTIONS, "suppress_tokens": list(self._suppression_tokens), "language": None},
            "deterministic_algorithms": self.torch.are_deterministic_algorithms_enabled(),
            "canonical_sample_len": self.model.dims.n_text_ctx // 2,
            "text_context_tokens": self.model.dims.n_text_ctx,
            "coverage_policy": "budget flags block qualification; shorter output does not prove speech coverage",
            "capacity_qualified": False,
        }

    def describe(self) -> dict:
        if self.model is None or self._description is None:
            raise RuntimeError("Reference engine is not ready")
        # Recompute actual resident state; never infer readiness from a label alone.
        return self._resident_description()

    def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000, *,
                       language: str | None = None, guard: Guard | None = None) -> dict:
        if self.model is None:
            raise RuntimeError("Reference engine is not ready")
        if sample_rate != 16000 or not isinstance(pcm, bytes) or not pcm or \
                len(pcm) % 2 or len(pcm) > 30 * 16000 * 2:
            raise ValueError("Expected at most30s of mono16kHz PCM16")
        from whisper.tokenizer import LANGUAGES
        if language is not None and language not in LANGUAGES:
            raise ValueError("Expected a canonical reference language code")
        if not self._request_lock.acquire(blocking=False):
            raise RuntimeError("Concurrent reference requests are not allowed")
        self._active_guard = guard
        try:
            self._check()
            self.model.decoding_observations = []
            samples = self.np.frombuffer(pcm, dtype="<i2").astype(self.np.float32) / 32768.0
            options = {**DECODING_OPTIONS, "suppress_tokens": list(self._suppression_tokens), "language": language}
            with self.torch.inference_mode():
                result = self.model.transcribe(samples, **options)
            self.torch.cuda.synchronize(self.device)
            self._check()
            # Preserve canonical text, language, segments and confidence fields verbatim.
            if not isinstance(result, dict) or not isinstance(result.get("text"), str) or \
                    not isinstance(result.get("segments"), list) or result.get("language") not in LANGUAGES:
                raise RuntimeError("Malformed reference transcription result")
            observations = [dict(row) for row in self.model.decoding_observations]
            return {**result, "decoding_observations": observations,
                    "decoding_budget_status": "budget_reached" if any(
                        row["budget_reached"] for row in observations) else "not_reached_no_coverage_claim"}
        finally:
            self._active_guard = None
            try:
                self.torch.cuda.synchronize(self.device)
                self.torch.cuda.empty_cache()
            finally:
                self._request_lock.release()

    def close(self) -> None:
        for handle in self._hooks:
            handle.remove()
        self._hooks = []
        self.model = None
        self._description = None
        if self.torch is not None and self.torch.cuda.is_initialized():
            self.torch.cuda.synchronize(self.device)
            self.torch.cuda.empty_cache()
