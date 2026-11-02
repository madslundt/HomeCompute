#!/usr/bin/env python3
"""Source-patch tests; the Docker build also applies these anchors to pinned vLLM."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
from pathlib import Path
import sys
import textwrap
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "whisper_runtime_patch", ROOT / "scripts/patch-vllm-whisper-runtime.py"
)
PATCH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PATCH
SPEC.loader.exec_module(PATCH)


class PinnedPatchTests(unittest.TestCase):
    def apply_fixture_patch(self, path):
        _, old, new = PATCH.PATCHES[path]
        original = old.encode()
        updated = PATCH.patch_bytes(original, sha256(original).hexdigest(), old, new)
        return old, new, updated.decode()

    def test_prompt_fix_uses_actual_pinned_source_patch_anchor(self):
        path = "model_executor/models/whisper.py"
        _, new, updated = self.apply_fixture_patch(path)
        self.assertIn("<|startofprev|>", new)
        self.assertIn("<|startofprev|>", updated)
        self.assertNotIn("<|prev|>", updated)

    def test_patched_verbose_branch_preserves_decoded_text_without_fabricated_timing(self):
        path = "entrypoints/openai/speech_to_text/speech_to_text.py"
        _, _, updated_block = self.apply_fixture_patch(path)
        function = "def capture(self, request, op, text_parts, total_segments, segment_class, start_time):\n"
        source = function + textwrap.indent(textwrap.dedent(updated_block), "    ") + \
            "\n    return text_parts, total_segments\n"
        namespace = {"SpeechToTextSegment": object}
        exec(compile(source, "patched-vllm-verbose-branch", "exec"), namespace)
        capture = namespace["capture"]

        class Model:
            @staticmethod
            def post_process_output(text):
                return text

        class Service:
            model_cls = Model

            def __init__(self, decoded_segments):
                self.decoded_segments = decoded_segments

            def _get_verbose_segments(self, **_kwargs):
                return self.decoded_segments

        request = types.SimpleNamespace(response_format="verbose_json")
        output = types.SimpleNamespace(text="decoded words", token_ids=(1, 2), logprobs=[{}])
        op = types.SimpleNamespace(outputs=[output])

        text_parts, segments = capture(
            Service([]), request, op, [], [], object, 0.0
        )
        self.assertEqual("".join(text_parts), "decoded words")
        self.assertEqual(segments, [])

        decoded_segment = types.SimpleNamespace(text="decoded words", start=1.0, end=2.0)
        text_parts, segments = capture(
            Service([decoded_segment]), request, op, [], [], object, 0.0
        )
        self.assertEqual("".join(text_parts), "decoded words")
        self.assertEqual(segments, [decoded_segment])

        trailing_output = types.SimpleNamespace(
            text="decoded words plus trailing passage", token_ids=(1, 2, 3), logprobs=[{}]
        )
        trailing_op = types.SimpleNamespace(outputs=[trailing_output])
        text_parts, segments = capture(
            Service([decoded_segment]), request, trailing_op, [], [], object, 0.0
        )
        self.assertEqual("".join(text_parts), "decoded words plus trailing passage")
        self.assertEqual(segments, [decoded_segment])

    def test_changed_pinned_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "checksum changed"):
            PATCH.patch_bytes(b"modified upstream source", "0" * 64, "old", "new")

    def test_missing_or_ambiguous_patch_anchor_is_rejected(self):
        original = b"old\nold\n"
        with self.assertRaisesRegex(ValueError, "anchor is missing or ambiguous"):
            PATCH.patch_bytes(original, sha256(original).hexdigest(), "old", "new")


if __name__ == "__main__":
    unittest.main()
