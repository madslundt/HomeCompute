"""Raw output, budget and immutable-artifact contracts; no model or GPU calls."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/qwen-asr-shadow"))
import engine
from model_files import RuntimeLoadError, verify_model

NAMES = {"en": "English", "da": "Danish"}


class OutputTests(unittest.TestCase):
    def test_repetitions_whitespace_and_mixed_language_are_preserved(self):
        text = " Ja. Ja.\n This is English.  Æble!\n"
        value = engine.parse_envelope("language Danish<asr_text>" + text, None, NAMES)
        self.assertEqual(value["text"], text)
        self.assertEqual(value["language"], "da")

    def test_unknown_or_multiple_language_label_does_not_force_text_language(self):
        result = engine.parse_envelope("language English,Danish<asr_text>Ja yes.", None, NAMES)
        self.assertIsNone(result["language"])
        self.assertEqual(result["language_label"], "English,Danish")
        self.assertEqual(result["text"], "Ja yes.")

    def test_empty_output_and_none_header_are_accepted_without_inventing_speech(self):
        for raw in ("", "language None<asr_text>"):
            self.assertEqual(engine.parse_envelope(raw, None, NAMES)["text"], "")

    def test_multiple_protocol_language_lines_preserve_mixed_text(self):
        result = engine.parse_envelope("language English\nlanguage Danish<asr_text>Ja yes.", None, NAMES)
        self.assertIsNone(result["language"])
        self.assertEqual(result["language_label"], "English\nDanish")
        self.assertEqual(result["text"], "Ja yes.")

    def test_only_first_protocol_marker_is_removed(self):
        value = engine.parse_envelope("language English<asr_text>literal <asr_text> words", None, NAMES)
        self.assertEqual(value["text"], "literal <asr_text> words")

    def test_forced_language_generation_is_already_plain_transcription(self):
        text = "language Danish<asr_text> was spoken literally."
        self.assertEqual(engine.parse_envelope(text, "en", NAMES)["text"], text)

    def test_malformed_auto_header_rejected(self):
        for raw in ("words without protocol", "language <asr_text>words", "language English\nDanish<asr_text>x"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                engine.parse_envelope(raw, None, NAMES)

    def test_budget_and_eos_are_observed_independently_of_coverage(self):
        ordinary = engine.generation_evidence([17, 151645], 10, 3)
        self.assertTrue(ordinary["eos_reached"])
        self.assertFalse(ordinary["budget_reached"])
        self.assertFalse(ordinary["coverage_claim"])
        capped = engine.generation_evidence([17] * 511 + [151645], 10, 3)
        self.assertTrue(capped["budget_reached"])
        incomplete = engine.generation_evidence([17] * 512, 10, 3)
        self.assertFalse(incomplete["eos_reached"])

    def test_invalid_token_or_prompt_evidence_rejected(self):
        for ids, prompt, audio in (([], 10, 3), ([False], 10, 3), ([151936], 10, 3),
                                   ([17] * 513, 10, 3), ([17], 3, 3), ([17], 0, 0)):
            with self.assertRaises(ValueError):
                engine.generation_evidence(ids, prompt, audio)

    def test_cpu_fallback_refused_before_loading(self):
        with self.assertRaises(ValueError):
            engine.QwenEngine("unused", device="cpu")

    def test_checkpoint_load_targets_cuda_without_full_cpu_model(self):
        observed = {}
        class Model:
            @staticmethod
            def from_pretrained(path, **kwargs):
                observed.update(kwargs)
                return "model", {"missing_keys": []}
        result = engine.load_checkpoint(Model, Path("synthetic"), "bf16-sentinel")
        self.assertEqual(result[0], "model")
        self.assertEqual(observed["device_map"], {"": "cuda:0"})
        self.assertEqual(observed["dtype"], "bf16-sentinel")
        for field in ("use_safetensors", "output_loading_info", "local_files_only"):
            self.assertIs(observed[field], True)
        self.assertIs(observed["trust_remote_code"], False)
        self.assertIs(observed["use_kernels"], False)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = b"synthetic checkpoint bytes"
        self.path = self.root / "model.safetensors"
        self.path.write_bytes(self.data)
        self.manifest = {"files": [{"path": self.path.name, "size": len(self.data),
                                   "sha256": hashlib.sha256(self.data).hexdigest()}]}

    def test_exact_artifact_passes(self):
        verify_model(self.root, self.manifest)

    def test_wrong_bytes_or_truncation_rejected(self):
        for raw in (b"x" * len(self.data), self.data[:-1]):
            self.path.write_bytes(raw)
            with self.assertRaises(RuntimeLoadError):
                verify_model(self.root, self.manifest)

    def test_unlisted_file_rejected(self):
        (self.root / "remote_code.py").write_text("synthetic")
        with self.assertRaises(RuntimeLoadError):
            verify_model(self.root, self.manifest)

    def test_symlink_and_nonregular_artifacts_rejected(self):
        self.path.unlink(); target = self.root.parent / (self.root.name + "-target")
        target.write_bytes(self.data); self.addCleanup(target.unlink)
        self.path.symlink_to(target)
        with self.assertRaises(RuntimeLoadError):
            verify_model(self.root, self.manifest)
        self.path.unlink(); self.path.mkdir()
        with self.assertRaises(RuntimeLoadError):
            verify_model(self.root, self.manifest)


if __name__ == "__main__":
    unittest.main()
