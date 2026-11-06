"""Pinned native Transformers ASR, with raw text and observed termination preserved."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import threading

from model_files import verify_model

ROOT = Path(__file__).resolve().parent
PIN = json.loads((ROOT / "model-pin.json").read_text())
ENGINE_VERSION = "qwen-native-hf-v2"
MAX_TOKENS = 512
OPTIONS = {"do_sample": False, "num_beams": 1, "max_new_tokens": MAX_TOKENS,
           "use_cache": True, "cache_implementation": "dynamic", "disable_compile": True,
           "language": None, "prompt": None,
           "attn_implementation": "sdpa", "audio_sampling_rate": 16000,
           "audio_dither": 0.0, "audio_truncation": False}


def parse_envelope(raw: str, language: str | None, names: dict[str, str]) -> dict:
    """Remove only the model's protocol header. Never collapse repeated speech.

    The publisher's parsed-output helper applies a repetition repair. This
    diagnostic deliberately preserves the raw generated transcription instead.
    """
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1_000_000:
        raise ValueError("Invalid model envelope")
    if language is not None:
        if language not in names:
            raise ValueError("Unsupported language")
        return {"text": raw, "language": language, "language_label": names[language]}
    if not raw.strip():
        return {"text": raw, "language": None, "language_label": "None"}
    prefix, marker, text = raw.partition("<asr_text>")
    prefix = prefix.strip()
    if not marker or len(prefix) > 256 or not prefix.casefold().startswith("language "):
        raise ValueError("Missing model protocol header")
    lines = prefix.splitlines()
    if any(not line.strip().casefold().startswith("language ") for line in lines):
        raise ValueError("Invalid model language header")
    labels = [line.strip()[len("language "):].strip() for line in lines]
    if not labels or any(not label for label in labels):
        raise ValueError("Invalid model language header")
    label = "\n".join(labels)
    code = next((key for key, value in names.items()
                 if all(value.casefold() == item.casefold() for item in labels)), None)
    return {"text": text, "language": code, "language_label": label}


def generation_evidence(ids: list[int], prompt_tokens: int, audio_tokens: int) -> dict:
    if (not isinstance(ids, list) or not ids or len(ids) > MAX_TOKENS
            or any(type(token) is not int or not 0 <= token < 151936 for token in ids)
            or type(prompt_tokens) is not int or not 0 < prompt_tokens <= 4096
            or type(audio_tokens) is not int or not 0 < audio_tokens < prompt_tokens):
        raise ValueError("Invalid generation evidence")
    return {"generated_tokens": len(ids), "max_new_tokens": MAX_TOKENS,
            "eos_reached": ids[-1] in (151643, 151645), "last_token_id": ids[-1],
            "budget_reached": len(ids) >= MAX_TOKENS,
            "prompt_tokens": prompt_tokens, "audio_tokens": audio_tokens,
            "coverage_claim": False}


def load_checkpoint(model_class, path, dtype):
    """Materialize checkpoint tensors on CUDA, without a complete CPU model copy."""
    return model_class.from_pretrained(
        str(path), dtype=dtype, device_map={"": "cuda:0"}, use_safetensors=True,
        use_kernels=False, attn_implementation="sdpa", output_loading_info=True,
        local_files_only=True, trust_remote_code=False)


class QwenEngine:
    def __init__(self, model_path, device="cuda:0"):
        if device != "cuda:0":
            raise ValueError("Only CUDA is supported; CPU fallback is disabled")
        self.path = Path(model_path)
        self.device = device
        self.model = self.processor = self.torch = self.np = None
        self.names = {}
        self.lock = threading.Lock()

    def file_identities(self):
        identities = {}
        for entry in PIN["files"]:
            info = (self.path / entry["path"]).stat(follow_symlinks=False)
            identities[entry["path"]] = (info.st_dev, info.st_ino, info.st_size,
                                         info.st_mtime_ns, info.st_ctime_ns)
        return identities

    def load(self):
        if self.model is not None:
            raise RuntimeError("Model already loaded")
        before = self.file_identities()
        verify_model(self.path, PIN)
        versions = {name: importlib.metadata.version(name) for name in PIN["packages"]}
        if versions != PIN["packages"]:
            raise RuntimeError("Package identity mismatch")
        # Runtime is offline; kernels and checkpoint code cannot be fetched.
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                          HF_HUB_DISABLE_TELEMETRY="1", CUBLAS_WORKSPACE_CONFIG=":4096:8")
        import numpy as np
        import torch
        import transformers
        from transformers.models.qwen3_asr.processing_qwen3_asr import LANGUAGE_CODE_TO_NAME

        for relative, digest in PIN["runtime_files"].items():
            path = Path(transformers.__file__).parent / relative
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError("Runtime source mismatch")
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError("Exactly one CUDA device is required")
        torch.set_num_threads(4)
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cuda.matmul.allow_tf32 = False
        self.torch, self.np = torch, np
        self.names = dict(LANGUAGE_CODE_TO_NAME)
        common = {"local_files_only": True, "trust_remote_code": False}
        try:
            self.processor = transformers.AutoProcessor.from_pretrained(str(self.path), **common)
            if type(self.processor).__module__ != "transformers.models.qwen3_asr.processing_qwen3_asr":
                raise RuntimeError("Unexpected processor implementation")
            if self.processor.feature_extractor.dither != 0.0:
                raise RuntimeError("Stochastic audio processing is forbidden")
            # The pinned loader initializes on meta and loads each tensor to its
            # explicit CUDA destination. No complete CPU model or offload is used.
            self.model, info = load_checkpoint(
                transformers.Qwen3ASRForConditionalGeneration, self.path, torch.bfloat16)
            if any(info.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
                raise RuntimeError("Checkpoint did not load completely")
            self.model.eval()
            self.describe()
            if self.file_identities() != before:
                raise RuntimeError("Checkpoint changed during loading")
            torch.cuda.synchronize(self.device)
            torch.cuda.empty_cache()
        except BaseException:
            self.close()
            raise

    def describe(self):
        if self.model is None:
            raise RuntimeError("Model is unavailable")
        parameters = list(self.model.parameters())
        if not parameters or any(str(p.device) != self.device or p.dtype != self.torch.bfloat16
                                 or p.is_meta for p in parameters):
            raise RuntimeError("Model parameters are not wholly CUDA BF16")
        count = sum(p.numel() for p in parameters)
        size = sum(p.numel() * p.element_size() for p in parameters)
        buffers = {name: str(value.device) for name, value in self.model.named_buffers()}
        if count != PIN["parameter_count"] or size != PIN["parameter_bytes"] or \
                any(device != self.device for device in buffers.values()):
            raise RuntimeError("Model inventory or residency mismatch")
        return {**{key: PIN[key] for key in ("model_repository", "model_sha256", "model_snapshot",
                                            "runtime", "runtime_commit")},
                "engine_version": ENGINE_VERSION,
                "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "device": self.device, "parameter_count": count, "parameter_bytes": size,
                "parameter_dtype": "bfloat16", "buffer_devices": buffers,
                "language_codes": sorted(self.names), "decoding_options": OPTIONS,
                "package_versions": {name: importlib.metadata.version(name) for name in PIN["packages"]},
                "deterministic_algorithms": self.torch.are_deterministic_algorithms_enabled(),
                "timing_policy": "not provided; no fabricated timestamps",
                "text_policy": "raw generation after protocol header; no repetition repair",
                "capacity_qualified": False}

    def transcribe_pcm(self, pcm: bytes, sample_rate=16000, *, language=None):
        if self.model is None:
            raise RuntimeError("Model unavailable")
        if sample_rate != 16000 or not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 960000:
            raise ValueError("Expected at most30s mono16kHz PCM16")
        if language is not None and language not in self.names:
            raise ValueError("Unsupported language")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Concurrent request refused")
        inputs = output = None
        try:
            self.torch.manual_seed(0)
            samples = self.np.frombuffer(pcm, dtype="<i2").astype(self.np.float32) / 32768.0
            with self.torch.inference_mode():
                inputs = self.processor.apply_transcription_request(
                    audio=samples, language=language, prompt=None, return_tensors="pt",
                    audio_kwargs={"sampling_rate": 16000, "truncation": False})
                inputs = {key: value.to(device=self.device, dtype=self.torch.bfloat16)
                          if value.is_floating_point() else value.to(self.device)
                          for key, value in inputs.items()}
                prompt_tokens = inputs["input_ids"].shape[-1]
                audio_tokens = int((inputs["input_ids"] == self.model.config.audio_token_id).sum().item())
                output = self.model.generate(
                    **inputs, do_sample=False, num_beams=1, max_new_tokens=MAX_TOKENS,
                    use_cache=True, cache_implementation="dynamic", disable_compile=True,
                    return_dict_in_generate=True, output_scores=False,
                    output_attentions=False, output_hidden_states=False)
                sequences = output.sequences.detach().cpu().tolist()
                if len(sequences) != 1:
                    raise RuntimeError("Unexpected model batch")
                if sequences[0][:prompt_tokens] != inputs["input_ids"][0].detach().cpu().tolist():
                    raise RuntimeError("Generation prompt identity changed")
                ids = sequences[0][prompt_tokens:]
                evidence = generation_evidence(ids, prompt_tokens, audio_tokens)
                text_ids = ids[:-1] if evidence["eos_reached"] else ids
                raw = self.processor.tokenizer.decode(
                    text_ids, skip_special_tokens=False, clean_up_tokenization_spaces=False)
                parsed = parse_envelope(raw, language, self.names)
                self.torch.cuda.synchronize(self.device)
                return {**parsed, "raw_decoded": raw, "generated_token_ids": ids,
                        "generation": evidence, "timing_qualified": False}
        finally:
            inputs = output = None
            try:
                self.torch.cuda.synchronize(self.device)
                self.torch.cuda.empty_cache()
            finally:
                self.lock.release()

    def close(self):
        self.model = self.processor = None
        if self.torch is not None and self.torch.cuda.is_initialized():
            self.torch.cuda.synchronize(self.device)
            self.torch.cuda.empty_cache()
