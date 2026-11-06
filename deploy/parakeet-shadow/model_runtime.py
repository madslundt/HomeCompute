"""Pinned, offline Parakeet shadow runtime. No service or production routing policy."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import stat
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

Guard = Callable[[], None]
SAMPLE_RATE = 16000
MAX_SECONDS = 180
MAX_NATIVE_TOKENS = 24000  # Output validation only; never passed to generation.
MANIFEST_PATH = Path(__file__).with_name('model-manifest.json')


class RuntimeGuardError(RuntimeError): pass
class RuntimeLoadError(RuntimeError): pass
class RuntimeInferenceError(RuntimeError): pass
class RuntimeNotReady(RuntimeError): pass
class RuntimeBusyError(RuntimeError): pass


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def _guard(callback: Guard | None) -> None:
    if callback is not None:
        try:
            callback()
        except Exception:  # noqa: BLE001 - callbacks are external; never expose their messages.
            raise RuntimeGuardError('Shadow runtime guard aborted') from None


def _verify_file(directory: Path, expected: dict[str, Any]) -> None:
    name = expected['path']
    if Path(name).name != name:
        raise RuntimeLoadError('Invalid model artifact name')
    descriptor = None
    try:
        descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected['size']:
            raise RuntimeLoadError('Model artifact size or type mismatch')
        sha256 = hashlib.sha256()
        git_blob = hashlib.sha1(f'blob {before.st_size}\0'.encode())
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > expected['size']:
                raise RuntimeLoadError('Model artifact changed during verification')
            sha256.update(chunk)
            git_blob.update(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(directory / name, follow_symlinks=False)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if total != expected['size'] or identity(before) != identity(after) or identity(after) != identity(path_after):
            raise RuntimeLoadError('Model artifact changed during verification')
        if expected.get('sha256', sha256.hexdigest()) != sha256.hexdigest():
            raise RuntimeLoadError('Model artifact SHA256 mismatch')
        if expected.get('git_blob_sha1', git_blob.hexdigest()) != git_blob.hexdigest():
            raise RuntimeLoadError('Model artifact Git identity mismatch')
    except OSError:
        raise RuntimeLoadError('Model artifact unavailable or unsafe') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def verify_model(directory: Path, manifest: dict[str, Any]) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeLoadError('Model directory unavailable or unsafe')
    expected = {entry['path'] for entry in manifest['files']}
    if {entry.name for entry in directory.iterdir()} != expected:
        raise RuntimeLoadError('Model directory differs from pinned allowlist')
    for entry in manifest['files']:
        _verify_file(directory, entry)


def validate_environment(manifest: dict[str, Any] | None = None) -> dict[str, str]:
    """Metadata-only check, also run at build time without initializing CUDA."""
    manifest = manifest or load_manifest()
    if sys.version_info[:2] != (3, 12) or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeLoadError('Shadow runtime requires the pinned ARM64 Python base')
    try:
        observed = {name: importlib.metadata.version(name) for name in manifest['packages']}
    except importlib.metadata.PackageNotFoundError:
        raise RuntimeLoadError('Required runtime package missing') from None
    if observed != manifest['packages']:
        raise RuntimeLoadError('Runtime package identity mismatch')
    return observed


def _dependencies():
    import numpy
    import torch
    import transformers
    return numpy, torch, transformers


def _stopping_guard(torch, transformers, callback: Guard | None):
    class ResourceStop(transformers.StoppingCriteria):
        aborted = False

        def __call__(self, input_ids, scores, **kwargs):
            try:
                _guard(callback)
            except RuntimeGuardError:
                self.aborted = True
            factory = torch.ones if self.aborted else torch.zeros
            return factory((input_ids.shape[0],), dtype=torch.bool, device=input_ids.device)
    return ResourceStop()


def _native_output(processor, output, encoder_frames: int) -> dict[str, Any]:
    ids = output.sequences.detach().cpu().tolist()
    durations = output.durations.detach().cpu().tolist()
    if (len(ids) != 1 or len(durations) != 1 or not 1 <= len(ids[0]) <= MAX_NATIVE_TOKENS
            or len(ids[0]) != len(durations[0])
            or any(type(value) is not int or not 0 <= value < 8193 for value in ids[0])
            or any(type(value) is not int or not 0 <= value <= 4 for value in durations[0])):
        raise RuntimeInferenceError('Invalid native token output')
    advanced_frames = sum(durations[0])
    if advanced_frames < encoder_frames:
        raise RuntimeInferenceError('Native decoder stopped before encoder exhaustion')
    decoded, timings = processor.decode(output.sequences, durations=output.durations, skip_special_tokens=True)
    if (not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], str)
            or not isinstance(timings, list) or len(timings) != 1 or not isinstance(timings[0], list)
            or len(timings[0]) > MAX_NATIVE_TOKENS):
        raise RuntimeInferenceError('Invalid native decoded output')
    for item in timings[0]:
        if (not isinstance(item, dict) or set(item) != {'token', 'start', 'end'}
                or not isinstance(item['token'], str)
                or any(type(item[key]) not in (int, float) or not math.isfinite(item[key]) for key in ('start', 'end'))
                or not 0 <= item['start'] <= item['end']):
            raise RuntimeInferenceError('Invalid native token timing')
    # Native predicted token times may be zero-width or extend beyond audio. Preserve
    # them as separate model evidence, never as aligned words or fabricated segments.
    return {'text': decoded[0], 'token_ids': ids[0], 'token_durations': durations[0],
            'token_timestamps': timings[0], 'encoder_frames': encoder_frames,
            'advanced_encoder_frames': advanced_frames, 'decoder_completed': True}


class ParakeetRuntime:
    def __init__(self, model_dir: Path, device: str = 'cuda', guard: Guard | None = None):
        if device not in ('cuda', 'cuda:0', 'cpu'):
            raise ValueError('Device must be cuda or cpu')
        self.model_dir = Path(model_dir)
        self.device = 'cuda:0' if device.startswith('cuda') else 'cpu'
        self.guard = guard
        self.manifest = load_manifest()
        self.ready = False
        self._model = self._processor = None
        self._torch = self._numpy = self._transformers = None
        self._parameter_bytes = None
        self._package_versions: dict[str, str] = {}
        self._lock = threading.Lock()

    def identity(self) -> dict[str, Any]:
        keys = ('model_repository', 'model_snapshot', 'model_sha256', 'runtime', 'runtime_commit', 'dtype')
        return {**{key: self.manifest[key] for key in keys}, 'device': self.device,
                'parameter_bytes': self._parameter_bytes, 'package_versions': dict(self._package_versions)}

    def _cleanup(self) -> None:
        if self._model is not None:
            for name in ('_encoder_finished', '_symbols_at_frame', '_step_durations'):
                if name in vars(self._model):
                    delattr(self._model, name)
        if self._torch is not None and self.device == 'cuda:0':
            self._torch.cuda.synchronize()
            self._torch.cuda.empty_cache()

    def load(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise RuntimeBusyError('Shadow runtime is busy')
        try:
            if self.ready:
                return
            _guard(self.guard)
            verify_model(self.model_dir, self.manifest)
            self._package_versions = validate_environment(self.manifest)
            # Set before importing/initializing CUDA. No model or kernel downloads.
            os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                              CUBLAS_WORKSPACE_CONFIG=':4096:8', NUMBA_CACHE_DIR='/tmp/numba')
            self._numpy, self._torch, self._transformers = _dependencies()
            torch = self._torch
            torch.set_num_threads(4)
            torch.use_deterministic_algorithms(True)
            torch.backends.cudnn.benchmark = False
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.allow_tf32 = False
            torch.backends.cuda.matmul.allow_tf32 = False
            if self.device == 'cuda:0' and not torch.cuda.is_available():
                raise RuntimeLoadError('CUDA unavailable; automatic fallback is forbidden')
            _guard(self.guard)
            common = {'local_files_only': True, 'trust_remote_code': False}
            self._processor = self._transformers.AutoProcessor.from_pretrained(str(self.model_dir), **common)
            # v5.22.1 initializes on meta and lazily mmaps safetensors on Linux.
            # No separate full state_dict, device_map/Accelerate, offload or quantization.
            self._model = self._transformers.ParakeetForTDT.from_pretrained(
                str(self.model_dir), dtype=torch.float32, use_safetensors=True, use_kernels=False,
                attn_implementation='sdpa', **common)
            self._model.to(self.device)
            self._model.eval()
            parameters = list(self._model.parameters())
            if any(item.dtype != torch.float32 or item.device.type != self.device.split(':')[0] for item in parameters):
                raise RuntimeLoadError('Model dtype or device mismatch')
            self._parameter_bytes = sum(item.numel() * item.element_size() for item in parameters)
            if not 0 < self._parameter_bytes <= 2508311120:
                raise RuntimeLoadError('Model parameter size mismatch')
            self._cleanup()
            _guard(self.guard)
            self.ready = True
        except BaseException as error:
            self._model = self._processor = None
            self._parameter_bytes = None
            self.ready = False
            try:
                self._cleanup()
            except Exception:  # noqa: BLE001 - preserve the primary failure, without private diagnostics.
                self.ready = False
            if isinstance(error, (RuntimeGuardError, RuntimeLoadError, KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeLoadError('Shadow model load failed') from None
        finally:
            self._lock.release()

    def transcribe_pcm(self, pcm: bytes, sample_rate: int = SAMPLE_RATE,
                       guard: Guard | None = None) -> dict[str, Any]:
        if (not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > MAX_SECONDS * SAMPLE_RATE * 2
                or type(sample_rate) is not int or sample_rate != SAMPLE_RATE):
            raise ValueError('Expected nonempty mono signed16 little-endian PCM at16000Hz, at most180s')
        if not self._lock.acquire(blocking=False):
            raise RuntimeBusyError('Shadow runtime is busy')
        inputs = output = samples = None
        failed = False
        def callback():
            # A request deadline must not replace the process-wide resource guard.
            _guard(self.guard)
            if guard is not self.guard:
                _guard(guard)
        try:
            if not self.ready:
                raise RuntimeNotReady('Shadow runtime is not ready')
            _guard(callback)
            samples = self._numpy.frombuffer(pcm, dtype='<i2').astype(self._numpy.float32) / 32768.0
            with self._torch.inference_mode():
                inputs = self._processor(samples, sampling_rate=SAMPLE_RATE, return_tensors='pt')
                inputs = {key: value.to(self.device) for key, value in inputs.items()}
                # Use the native encoder's own geometry on the real feature mask;
                # do not infer progress from requested duration or output text.
                encoder_lengths = self._model.encoder._get_subsampling_output_length(
                    inputs['attention_mask'].sum(-1)).detach().cpu().tolist()
                if (len(encoder_lengths) != 1 or type(encoder_lengths[0]) is not int
                        or not 0 < encoder_lengths[0] <= 2251):
                    raise RuntimeInferenceError('Invalid native encoder length')
                _guard(callback)
                criterion = _stopping_guard(self._torch, self._transformers, callback)
                output = self._model.generate(
                    **inputs, do_sample=False, num_beams=1, return_dict_in_generate=True,
                    output_scores=False, output_attentions=False, output_hidden_states=False,
                    stopping_criteria=self._transformers.StoppingCriteriaList([criterion]))
                if criterion.aborted:
                    raise RuntimeGuardError('Shadow runtime guard aborted')
                _guard(callback)
                result = _native_output(self._processor, output, encoder_lengths[0])
            _guard(callback)
            return result
        except BaseException as error:
            failed = True
            if isinstance(error, (RuntimeGuardError, RuntimeNotReady, KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeInferenceError('Shadow inference failed') from None
        finally:
            inputs = output = samples = None
            try:
                self._cleanup()
            except Exception:  # noqa: BLE001 - CUDA cleanup failures cannot publish a successful response.
                self.ready = False
                if not failed:
                    raise RuntimeInferenceError('Shadow runtime cleanup failed') from None
            finally:
                self._lock.release()
