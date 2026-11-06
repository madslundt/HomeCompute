#!/usr/bin/env python3
"""Synthetic binary checks plus optional CPU-only canonical architecture parity.

Fixture model.py is byte-exact OpenAI Whisper31243bad (MIT, adjacent LICENSE).
No weights, speech, remote imports, GPU calls, or real model construction occur.
"""
import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/whisper-reference-shadow"))
import ggml_loader as loader

try:
    import torch
except ImportError:
    torch = None

DIMS = dict(zip(loader.HEADER_KEYS, (32, 4, 8, 2, 2, 8, 8, 2, 2, 2)))


def binary(entries, dimensions=DIMS):
    prefix = struct.pack("<12i", 0x67676D6C,
                         *(dimensions[k] for k in loader.HEADER_KEYS), 1)
    filters = struct.pack("<f", 0.125) * (dimensions["n_mels"] * 201)
    vocab = struct.pack("<i", 2) + struct.pack("<i", 1) + b"a" + struct.pack("<i", 0)
    data = bytearray(prefix + struct.pack("<ii", dimensions["n_mels"], 201) + filters + vocab)
    for name, shape, type_id, payload in entries:
        encoded = name.encode("ascii")
        data.extend(struct.pack("<iii", len(shape), len(encoded), type_id))
        data.extend(struct.pack("<" + "i" * len(shape), *reversed(shape)))
        data.extend(encoded)
        data.extend(payload)
    return bytes(data), hashlib.sha256(filters).hexdigest(), hashlib.sha256(vocab).hexdigest()


class BinaryTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            ("encoder.conv1.bias", (8, 1), 0, struct.pack("<8f", *range(8))),
            ("decoder.token_embedding.weight", (32, 8), 1, struct.pack("<e", 0.5) * 256),
        ]
        self.shapes = {"encoder.conv1.bias": (8,), "decoder.token_embedding.weight": (32, 8)}

    def inspect(self, entries=None, transform=lambda x: x, **overrides):
        raw, filters, vocab = binary(self.entries if entries is None else entries)
        with tempfile.TemporaryFile() as f:
            f.write(transform(raw)); f.flush()
            return loader.inspect_layout(f, dimensions=DIMS, shapes=self.shapes,
                                         filter_sha256=overrides.get("filter_sha256", filters),
                                         vocab_sha256=overrides.get("vocab_sha256", vocab))

    def test_exact_shapes_and_conv_bias_inverse(self):
        records = self.inspect()
        self.assertEqual([(r.name, r.shape, r.type_id, r.nbytes) for r in records],
                         [("encoder.conv1.bias", (8,), 0, 32),
                          ("decoder.token_embedding.weight", (32, 8), 1, 512)])
        self.assertEqual(records[1].offset + records[1].nbytes, len(binary(self.entries)[0]))

    def test_duplicate_unknown_missing_and_wrong_shape_refused(self):
        variants = [self.entries + [self.entries[0]], self.entries[:1],
                    [("unknown.weight", *self.entries[0][1:]), self.entries[1]],
                    [(self.entries[0][0], (1, 8), *self.entries[0][2:]), self.entries[1]]]
        for entries in variants:
            with self.subTest(entries=entries[0][:3]), self.assertRaises(loader.ModelFormatError):
                self.inspect(entries)

    def test_encoded_dtype_must_match_canonical_storage_policy(self):
        for typ in (1, 2, -1):
            entries = [(self.entries[0][0], (8, 1), typ, self.entries[0][3]), self.entries[1]]
            with self.subTest(type=typ), self.assertRaises(loader.ModelFormatError):
                self.inspect(entries)

    def test_truncated_header_payload_and_trailing_data_refused(self):
        for size in (1, 20, 49, len(binary(self.entries)[0]) - 1):
            with self.subTest(size=size), self.assertRaises(loader.ModelFormatError):
                self.inspect(transform=lambda value: value[:size])
        with self.assertRaises(loader.ModelFormatError):
            self.inspect(transform=lambda value: value + b"x")

    def test_filter_and_tokenizer_linkage_is_mandatory(self):
        for field in ("filter_sha256", "vocab_sha256"):
            with self.subTest(field=field), self.assertRaises(loader.ModelFormatError):
                self.inspect(**{field: "0" * 64})

    def test_full_artifact_hash_size_and_guard(self):
        data = b"synthetic verified weight bytes"
        with tempfile.TemporaryFile() as f:
            f.write(data); f.flush()
            calls = []
            loader.verify_file(f, len(data), hashlib.sha256(data).hexdigest(), lambda: calls.append(1))
            self.assertTrue(calls)
            for size, digest in [(len(data) + 1, hashlib.sha256(data).hexdigest()),
                                 (len(data), "0" * 64)]:
                with self.assertRaises(loader.ModelFormatError):
                    loader.verify_file(f, size, digest, lambda: None)
            def abort():
                raise RuntimeError("synthetic resource guard")
            with self.assertRaisesRegex(RuntimeError, "resource guard"):
                loader.verify_file(f, len(data), hashlib.sha256(data).hexdigest(), abort)

    def test_production_cpu_fallback_rejected_before_any_model_operation(self):
        with self.assertRaisesRegex(ValueError, "CPU fallback"):
            loader.load_approved_model(Path("unused"), None, None, device="cpu", guard=lambda: None)


def reference_fixture():
    name = "synthetic_official_whisper"
    package = types.ModuleType(name); package.__path__ = []
    sys.modules[name] = package
    def no_decode(*args, **kwargs):
        raise AssertionError("No ASR decoding is permitted in toy architecture tests")
    for suffix, functions in {"decoding": ["decode", "detect_language"],
                              "transcribe": ["transcribe"]}.items():
        module = types.ModuleType(name + "." + suffix)
        for function in functions:
            setattr(module, function, no_decode)
        sys.modules[module.__name__] = module
    path = ROOT / "tests/fixtures/whisper-reference/model.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == \
        "473491a97554ad1d0beaaef6fe91d965f12785b466b93ebb61024307b50ad683"
    spec = importlib.util.spec_from_file_location(name + ".model", path)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(torch is not None, "CPU Torch toy proof runs separately; no dependency download")
class CanonicalToyTests(unittest.TestCase):
    def setUp(self):
        self.reference = reference_fixture()
        torch.manual_seed(7)
        self.canonical = self.reference.Whisper(self.reference.ModelDimensions(**DIMS)).eval()
        values, entries = {}, []
        for name, item in self.canonical.state_dict().items():
            # Same GGML-rounded values in BOTH models, not a float32-vs-float16 weight comparison.
            dtype = torch.float32 if item.ndim < 2 or name in loader.CONV_BIASES | loader.POSITIONS else torch.float16
            value = (torch.randn(item.shape) * .1).to(dtype)
            values[name] = value
            shape = tuple(value.shape) + ((1,) if name in loader.CONV_BIASES else ())
            entries.append((name, shape, 0 if dtype == torch.float32 else 1, value.numpy().tobytes()))
        self.canonical.load_state_dict(values)
        self.values = values
        self.raw, self.filter_sha, self.vocab_sha = binary(entries)
        self.streamed = loader.construct_meta_model(self.reference, torch, DIMS)
        self.assertTrue(all(v.is_meta for v in self.streamed.state_dict().values()))
        self.assertEqual(str(self.streamed.alignment_heads.device), "cpu")
        with tempfile.TemporaryFile() as f:
            f.write(self.raw); f.flush()
            records = loader.inspect_layout(f, dimensions=DIMS,
                         shapes={k: tuple(v.shape) for k, v in self.streamed.state_dict().items()},
                         filter_sha256=self.filter_sha, vocab_sha256=self.vocab_sha)
            loader.stream_tensors(self.streamed, f, records, torch, "cpu", lambda: None)

    def test_state_names_shapes_values_and_encoded_dtypes_exact(self):
        self.assertEqual(set(self.streamed.state_dict()), set(self.canonical.state_dict()))
        for name, actual in self.streamed.state_dict().items():
            self.assertEqual(actual.dtype, self.values[name].dtype, name)
            torch.testing.assert_close(actual, self.values[name], rtol=0, atol=0)
        torch.testing.assert_close(self.streamed.decoder.mask, self.canonical.decoder.mask, rtol=0, atol=0)
        torch.testing.assert_close(self.streamed.alignment_heads.to_dense(),
                                   self.canonical.alignment_heads.to_dense(), rtol=0, atol=0)
        self.assertEqual(type(self.streamed), type(self.canonical))
        for method in ("forward", "embed_audio", "logits", "decode", "detect_language",
                       "transcribe", "install_kv_cache_hooks"):
            self.assertIs(getattr(type(self.streamed), method), getattr(type(self.canonical), method))

    def test_encoder_and_fullprefix_logits_are_exact(self):
        mel = torch.randn(1, DIMS["n_mels"], DIMS["n_audio_ctx"] * 2).half()
        tokens = torch.tensor([[1, 4, 7]])
        with torch.inference_mode():
            expected_features = self.canonical.embed_audio(mel)
            actual_features = self.streamed.embed_audio(mel)
            torch.testing.assert_close(actual_features, expected_features, rtol=0, atol=0)
            torch.testing.assert_close(self.streamed.logits(tokens, actual_features),
                                       self.canonical.logits(tokens, expected_features), rtol=0, atol=0)

    def test_incremental_kv_cache_logits_match_canonical(self):
        mel = torch.randn(1, DIMS["n_mels"], DIMS["n_audio_ctx"] * 2).half()
        caches, hooks = [], []
        with torch.inference_mode():
            features = [model.embed_audio(mel) for model in (self.canonical, self.streamed)]
            try:
                for model in (self.canonical, self.streamed):
                    cache, handles = model.install_kv_cache_hooks(); caches.append(cache); hooks.extend(handles)
                for tokens in (torch.tensor([[1, 4]]), torch.tensor([[7]]), torch.tensor([[8]])):
                    results = [model.decoder(tokens, feature, kv_cache=cache)
                               for model, feature, cache in zip((self.canonical, self.streamed), features, caches)]
                    torch.testing.assert_close(results[0], results[1], rtol=0, atol=0)
                self.assertEqual([tuple(x.shape) for x in caches[0].values()],
                                 [tuple(x.shape) for x in caches[1].values()])
            finally:
                for hook in hooks:
                    hook.remove()


if __name__ == "__main__":
    unittest.main()
