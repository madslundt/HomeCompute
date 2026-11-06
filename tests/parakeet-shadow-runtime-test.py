#!/usr/bin/env python3
"""Offline ownership, guard and exact-output tests; no weights or GPU required."""
from __future__ import annotations

import hashlib
import importlib.util
import struct
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('parakeet_runtime', ROOT / 'deploy/parakeet-shadow/model_runtime.py')
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class Tensor:
    def __init__(self, values):
        self.values = values
        self.shape = (len(values), len(values[0])) if values and isinstance(values[0], list) else (len(values),)
        self.device = 'cuda:0'

    def detach(self): return self
    def cpu(self): return self
    def tolist(self): return self.values


class Audio(list):
    def astype(self, _dtype): return self
    def __truediv__(self, divisor): return Audio(value / divisor for value in self)


def dependencies():
    numpy = NS(float32='float32', frombuffer=lambda raw, dtype: Audio(
        struct.unpack('<' + 'h' * (len(raw) // 2), raw)))
    torch = NS(float32='float32', bool='bool', inference_mode=nullcontext,
               use_deterministic_algorithms=Mock(), set_num_threads=Mock(),
               cuda=NS(is_available=Mock(return_value=True), synchronize=Mock(), empty_cache=Mock()),
               backends=NS(cudnn=NS(), cuda=NS(matmul=NS())),
               zeros=lambda shape, **kwargs: [False] * shape[0],
               ones=lambda shape, **kwargs: [True] * shape[0])
    model = Mock()
    model.to.return_value = model
    model.eval.return_value = model
    model.parameters.return_value = [NS(numel=lambda: 3, element_size=lambda: 4,
                                       dtype='float32', device=NS(type='cuda'))]
    model.generate.return_value = NS(sequences=Tensor([[8192, 5, 6]]), durations=Tensor([[0, 2, 0]]))
    model.encoder._get_subsampling_output_length.return_value = Tensor([2])
    inputs = {'input_features': Mock(), 'attention_mask': Mock()}
    for value in inputs.values(): value.to.return_value = value
    processor = Mock(return_value=inputs)
    processor.decode.return_value = (['  Exact Æ text.\n'], [[
        {'token': 'Exact', 'start': 0.0, 'end': 0.16},
        {'token': '.', 'start': 0.16, 'end': 0.16}]])
    transformers = NS(ParakeetForTDT=NS(from_pretrained=Mock(return_value=model)),
                      AutoProcessor=NS(from_pretrained=Mock(return_value=processor)),
                      StoppingCriteria=object, StoppingCriteriaList=list)
    return numpy, torch, transformers, model, processor


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.np, self.torch, self.transformers, self.model, self.processor = dependencies()
        self.addCleanup(patch.stopall)
        patch.object(RUNTIME, 'verify_model', return_value=None).start()
        patch.object(RUNTIME, 'validate_environment', return_value={}).start()
        patch.object(RUNTIME, '_dependencies', return_value=(self.np, self.torch, self.transformers)).start()
        self.runtime = RUNTIME.ParakeetRuntime(Path(self.tmp.name), 'cuda')

    def test_constructor_is_lazy_and_load_is_local_fp32_single_device(self):
        RUNTIME._dependencies.assert_not_called()
        self.runtime.load()
        kwargs = self.transformers.ParakeetForTDT.from_pretrained.call_args.kwargs
        self.assertTrue(kwargs['local_files_only'])
        self.assertFalse(kwargs['trust_remote_code'])
        self.assertTrue(kwargs['use_safetensors'])
        self.assertFalse(kwargs['use_kernels'])
        self.assertEqual(kwargs['dtype'], 'float32')
        self.assertNotIn('device_map', kwargs)
        self.model.to.assert_called_once_with('cuda:0')
        self.torch.use_deterministic_algorithms.assert_called_once_with(True)
        self.assertEqual(self.runtime.identity()['parameter_bytes'], 12)
        self.assertEqual(self.runtime.identity()['device'], 'cuda:0')
        self.assertFalse(self.torch.backends.cudnn.benchmark)
        self.assertFalse(self.torch.backends.cudnn.allow_tf32)
        self.assertFalse(self.torch.backends.cuda.matmul.allow_tf32)
        self.assertTrue(self.torch.backends.cudnn.deterministic)

    def test_request_guard_cannot_override_global_resource_guard(self):
        self.runtime.load()
        self.runtime.guard = Mock(side_effect=RuntimeError('memory floor'))
        with self.assertRaises(RUNTIME.RuntimeGuardError):
            self.runtime.transcribe_pcm(b'\0\0', guard=lambda: None)
        self.processor.assert_not_called()

    def test_not_loaded_and_invalid_device_refused_without_model_calls(self):
        with self.assertRaises(RUNTIME.RuntimeNotReady): self.runtime.transcribe_pcm(b'\0\0')
        with self.assertRaises(ValueError): RUNTIME.ParakeetRuntime(Path(self.tmp.name), 'auto')
        self.model.generate.assert_not_called()

    def test_exact_pcm_text_and_native_tokens_without_artificial_segments(self):
        self.runtime.load()
        pcm = struct.pack('<hhhh', -32768, -1, 0, 32767)
        result = self.runtime.transcribe_pcm(pcm)
        self.assertEqual(list(self.processor.call_args.args[0]), [-1, -1 / 32768, 0, 32767 / 32768])
        self.assertEqual(result['text'], '  Exact Æ text.\n')
        self.assertEqual(result['token_ids'], [8192, 5, 6])
        self.assertEqual(result['token_durations'], [0, 2, 0])
        self.assertEqual(result['token_timestamps'][1]['start'], result['token_timestamps'][1]['end'])
        self.assertNotIn('segments', result)
        self.assertNotIn('language', result)
        kwargs = self.model.generate.call_args.kwargs
        self.assertFalse(kwargs['do_sample'])
        self.assertEqual(kwargs['num_beams'], 1)
        self.assertNotIn('max_new_tokens', kwargs)
        self.assertNotIn('max_length', kwargs)
        self.assertEqual(result['encoder_frames'], 2)
        self.assertEqual(result['advanced_encoder_frames'], 2)
        self.assertTrue(result['decoder_completed'])
        self.torch.cuda.synchronize.assert_called()
        self.torch.cuda.empty_cache.assert_called()

    def test_preload_guard_refuses_before_import_or_artifact_reads(self):
        def guard(): raise ValueError('private detail must not leak')
        self.runtime.guard = guard
        with self.assertRaisesRegex(RUNTIME.RuntimeGuardError, '^Shadow runtime guard aborted$'):
            self.runtime.load()
        RUNTIME._dependencies.assert_not_called()
        RUNTIME.verify_model.assert_not_called()

    def test_postload_guard_failure_discards_model(self):
        guard = Mock(side_effect=[None, None, RuntimeError('private')])
        self.runtime.guard = guard
        with self.assertRaises(RUNTIME.RuntimeGuardError): self.runtime.load()
        self.assertFalse(self.runtime.ready)
        self.assertIsNone(self.runtime.identity()['parameter_bytes'])

    def test_decoder_guard_stops_then_rejects_partial_text_and_recovers(self):
        self.runtime.load()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls >= 3: raise RuntimeError('deadline')
        def generate(**kwargs):
            criterion = kwargs['stopping_criteria'][0]
            self.assertEqual(criterion(Tensor([[8192]]), None), [True])
            self.model._step_durations = ['temporary tensor']
            return NS(sequences=Tensor([[8192, 5]]), durations=Tensor([[0, 1]]))
        self.model.generate.side_effect = generate
        with self.assertRaises(RUNTIME.RuntimeGuardError): self.runtime.transcribe_pcm(b'\0\0', guard=guard)
        self.processor.decode.assert_not_called()
        self.assertNotIn('_step_durations', vars(self.model))
        self.model.generate.side_effect = None
        self.assertEqual(self.runtime.transcribe_pcm(b'\0\0')['text'], '  Exact Æ text.\n')

    def test_generation_failure_cleans_up_and_never_returns_partial(self):
        self.runtime.load()
        self.model.generate.side_effect = ValueError('private text')
        with self.assertRaisesRegex(RUNTIME.RuntimeInferenceError, '^Shadow inference failed$'):
            self.runtime.transcribe_pcm(b'\0\0')
        self.processor.decode.assert_not_called()
        self.torch.cuda.empty_cache.assert_called()

    def test_guard_runs_after_generation_before_output_publication(self):
        self.runtime.load()
        guard = Mock(side_effect=[None, None, RuntimeError('resource')])
        with self.assertRaises(RUNTIME.RuntimeGuardError): self.runtime.transcribe_pcm(b'\0\0', guard=guard)
        self.processor.decode.assert_not_called()

    def test_invalid_pcm_bounds_and_rate_fail_before_processing(self):
        self.runtime.load()
        for raw, rate in [(b'', 16000), (b'1', 16000), (b'\0\0', 8000),
                          (b'\0' * (180 * 16000 * 2 + 2), 16000)]:
            with self.subTest(size=len(raw), rate=rate), self.assertRaises(ValueError):
                self.runtime.transcribe_pcm(raw, rate)
        self.processor.assert_not_called()

    def test_exact_180_seconds_is_allowed_without_implicit_chunking(self):
        self.runtime.load()
        self.runtime.transcribe_pcm(b'\0' * (180 * 16000 * 2))
        self.assertEqual(self.processor.call_count, 1)
        self.assertEqual(len(self.processor.call_args.args[0]), 180 * 16000)

    def test_batch_cardinality_and_malformed_native_metadata_rejected(self):
        self.runtime.load()
        for decoded in [([], []), (['a', 'b'], [[], []]), ('text', []),
                        (['a'], [[{'token': 'a', 'start': float('nan'), 'end': 1}]])]:
            self.processor.decode.return_value = decoded
            with self.subTest(decoded=decoded), self.assertRaises(RUNTIME.RuntimeInferenceError):
                self.runtime.transcribe_pcm(b'\0\0')

    def test_invalid_sequence_duration_pairs_cannot_publish_text(self):
        self.runtime.load()
        for ids, durations in [([[1]], [[0, 1]]), ([[1]], [[-1]]), ([[8193]], [[1]]),
                               ([[1]], [[5]]), ([[True]], [[1]]), ([[], []], [[], []])]:
            self.model.generate.return_value = NS(sequences=Tensor(ids), durations=Tensor(durations))
            with self.subTest(ids=ids), self.assertRaises(RUNTIME.RuntimeInferenceError):
                self.runtime.transcribe_pcm(b'\0\0')
        self.processor.decode.assert_not_called()

    def test_early_eos_or_native_capacity_exhaustion_cannot_publish_partial_text(self):
        self.runtime.load()
        self.model.encoder._get_subsampling_output_length.return_value = Tensor([2250])
        for ids, durations in [([8192, 5, 3], [0, 2, 0]),
                               ([8192] + [5] * 22500, [0] * 22501)]:
            self.model.generate.return_value = NS(sequences=Tensor([ids]), durations=Tensor([durations]))
            with self.subTest(steps=len(ids)), self.assertRaises(RUNTIME.RuntimeInferenceError):
                self.runtime.transcribe_pcm(b'\0' * (180 * 16000 * 2))
        self.processor.decode.assert_not_called()

    def test_final_tdt_advance_may_cross_encoder_end_without_clamping(self):
        self.runtime.load()
        self.model.encoder._get_subsampling_output_length.return_value = Tensor([1])
        result = self.runtime.transcribe_pcm(b'\0\0')
        self.assertEqual((result['encoder_frames'], result['advanced_encoder_frames']), (1, 2))

    def test_native_timing_overrun_preserved_as_optional_evidence_without_clamping(self):
        self.runtime.load()
        timing = {'token': 'word', 'start': 0.08, 'end': 0.32}
        self.processor.decode.return_value = (['word'], [[timing]])
        self.assertEqual(self.runtime.transcribe_pcm(b'\0\0')['token_timestamps'], [timing])

    def test_cleanup_failure_cannot_publish_success_or_claim_ready(self):
        self.runtime.load()
        self.torch.cuda.synchronize.side_effect = RuntimeError('device detail')
        with self.assertRaisesRegex(RUNTIME.RuntimeInferenceError, '^Shadow runtime cleanup failed$'):
            self.runtime.transcribe_pcm(b'\0\0')
        self.assertFalse(self.runtime.ready)

    def test_cpu_is_explicit_and_never_initializes_cuda(self):
        runtime = RUNTIME.ParakeetRuntime(Path(self.tmp.name), 'cpu')
        self.model.parameters.return_value[0].device.type = 'cpu'
        runtime.load()
        runtime.transcribe_pcm(b'\0\0')
        self.model.to.assert_called_once_with('cpu')
        self.torch.cuda.is_available.assert_not_called()
        self.torch.cuda.synchronize.assert_not_called()
        self.torch.cuda.empty_cache.assert_not_called()

    def test_cuda_unavailable_has_no_cpu_fallback(self):
        self.torch.cuda.is_available.return_value = False
        with self.assertRaises(RUNTIME.RuntimeLoadError): self.runtime.load()
        self.model.to.assert_not_called()
        self.assertFalse(self.runtime.ready)

    def test_requests_are_not_concurrent(self):
        self.runtime.load()
        self.runtime._lock.acquire()
        try:
            with self.assertRaises(RUNTIME.RuntimeBusyError): self.runtime.transcribe_pcm(b'\0\0')
        finally: self.runtime._lock.release()


class ArtifactTests(unittest.TestCase):
    def test_exact_bytes_required_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            content = b'synthetic artifact'
            artifact = directory / 'config.json'
            artifact.write_bytes(content)
            expected = {'path': 'config.json', 'size': len(content),
                        'git_blob_sha1': hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()}
            RUNTIME._verify_file(directory, expected)
            artifact.write_bytes(b'wrong artifact!!!!')
            with self.assertRaises(RUNTIME.RuntimeLoadError): RUNTIME._verify_file(directory, expected)
            artifact.unlink()
            (directory / 'real').write_bytes(content)
            artifact.symlink_to(directory / 'real')
            with self.assertRaises(RUNTIME.RuntimeLoadError): RUNTIME._verify_file(directory, expected)

    def test_weight_sha_and_no_extra_model_files_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            payload = b'fake weights'
            (directory / 'model.safetensors').write_bytes(payload)
            entry = {'path': 'model.safetensors', 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
            RUNTIME.verify_model(directory, {'files': [entry]})
            (directory / 'unreviewed.py').write_text('')
            with self.assertRaises(RUNTIME.RuntimeLoadError): RUNTIME.verify_model(directory, {'files': [entry]})
            (directory / 'unreviewed.py').unlink()
            (directory / 'model.safetensors').write_bytes(b'evil weights')
            with self.assertRaises(RUNTIME.RuntimeLoadError): RUNTIME.verify_model(directory, {'files': [entry]})

    def test_mutation_during_hashing_and_path_escape_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            file = directory / 'config.json'
            file.write_bytes(b'original')
            expected = {'path': file.name, 'size': 8, 'sha256': hashlib.sha256(b'original').hexdigest()}
            original_read = RUNTIME.os.read
            replaced = False
            def read(descriptor, size):
                nonlocal replaced
                result = original_read(descriptor, size)
                if not replaced:
                    file.rename(directory / 'retained')
                    file.write_bytes(b'original')
                    replaced = True
                return result
            with patch.object(RUNTIME.os, 'read', side_effect=read), self.assertRaises(RUNTIME.RuntimeLoadError):
                RUNTIME._verify_file(directory, expected)
            with self.assertRaises(RUNTIME.RuntimeLoadError):
                RUNTIME._verify_file(directory, {**expected, 'path': '../config.json'})

    def test_environment_refuses_vendor_torch_replacement(self):
        manifest = RUNTIME.load_manifest()
        with patch.object(RUNTIME.platform, 'machine', return_value='aarch64'), \
                patch.object(RUNTIME.sys, 'version_info', (3, 12)), \
                patch.object(RUNTIME.importlib.metadata, 'version', side_effect=manifest['packages'].__getitem__):
            self.assertEqual(RUNTIME.validate_environment(manifest), manifest['packages'])
            with patch.object(RUNTIME.importlib.metadata, 'version', return_value='different'), \
                    self.assertRaises(RUNTIME.RuntimeLoadError): RUNTIME.validate_environment(manifest)

    def test_manifest_and_lock_match_reviewed_public_identities(self):
        manifest = RUNTIME.load_manifest()
        self.assertEqual(manifest['model_snapshot'], '541d1f99c6b0c3cd0b11a95167540bb8edefd82b')
        self.assertEqual(manifest['model_sha256'], '3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11')
        self.assertEqual(len(manifest['files']), 7)
        self.assertEqual(manifest['runtime_commit'], '446a570e62b04a89ea001604ada7366baac2ea67')
        lock = (ROOT / 'deploy/parakeet-shadow/requirements.lock').read_text()
        self.assertNotIn('torch==', lock)
        self.assertEqual(lock.count('--hash=sha256:'), 6)


if __name__ == '__main__': unittest.main()
