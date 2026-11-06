#!/usr/bin/env python3
"""Compile the exact pinned final-window seek branch; no audio or model needed."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "seek_patch", ROOT / "deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


class SeekRecoveryTests(unittest.TestCase):
    def test_early_completed_segment_keeps_unprocessed_audio(self):
        self.assertEqual(self.run_branch(700), 700)

    def test_complete_or_overrunning_timestamp_stops_at_input_end(self):
        self.assertEqual(self.run_branch(2300, remaining=2299), 2299)
        self.assertEqual(self.run_branch(3000), 3000)

    def test_unsafe_or_explicit_modes_preserve_full_window_progress(self):
        for delta in (0, 1, 9):
            self.assertEqual(self.run_branch(delta), 3000)
        for flag in ("silence", "single", "untimed"):
            self.assertEqual(self.run_branch(700, mode=flag), 3000)

    def test_non_single_ending_path_is_unchanged(self):
        self.assertEqual(self.run_branch(700, ending=False), 700)

    def test_mutated_or_duplicate_anchor_is_refused(self):
        old = PATCH.SEEK_RECOVERY_PATCH[0]
        for source in ("", old + old):
            with self.assertRaises(ValueError):
                PATCH.patch_seek_recovery(source)

    def run_branch(self, delta, remaining=3000, mode="speech", ending=True):
        fixture = (ROOT / "tests/fixtures/whisper-cpp-window-trace.cpp").read_text()
        old = "            if (single_timestamp_ending) {" + fixture.split(
            "            if (single_timestamp_ending) {", 1)[1].split(
                "            // update audio window", 1)[0].rstrip() + "\n"
        if hasattr(PATCH, "patch_seek_recovery"):
            self.assertEqual(old, PATCH.SEEK_RECOVERY_PATCH[0])
            branch = PATCH.patch_seek_recovery(old)
        else:
            branch = old
        source = """#include <algorithm>
#include <cstdio>
#define WHISPER_LOG_DEBUG(...) ((void)0)
constexpr int WHISPER_CHUNK_SIZE=30, delta_min=10;
struct { bool single_segment=false, no_timestamps=false; } params;
int main() {
"""
        source += f"int seek=0, seek_end={remaining}, seek_delta={delta};\n"
        source += f"bool single_timestamp_ending={'true' if ending else 'false'};\n"
        source += f"bool is_no_speech={'true' if mode == 'silence' else 'false'};\n"
        source += f"params.single_segment={'true' if mode == 'single' else 'false'};\n"
        source += f"params.no_timestamps={'true' if mode == 'untimed' else 'false'};\n"
        source += branch + '\nstd::printf("%d",seek_delta);\n}\n'
        compiler = shutil.which("c++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); cpp = path / "probe.cpp"; binary = path / "probe"
            cpp.write_text(source)
            subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(binary)],
                           check=True, capture_output=True, timeout=30)
            return int(subprocess.check_output([str(binary)], timeout=5))


if __name__ == "__main__":
    unittest.main()
