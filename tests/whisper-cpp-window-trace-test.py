#!/usr/bin/env python3
"""Compile actual pinned seek/trace excerpts without models, audio, network or GPU."""
from __future__ import annotations

from hashlib import sha256
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "trace_patch", ROOT / "deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
FIXTURE = ROOT / "tests/fixtures/whisper-cpp-window-trace.cpp"
LIMIT = 1024 * 1024

PREAMBLE = r"""
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
#include <string>
#include <random>
constexpr int WHISPER_CHUNK_SIZE = 30;
#define WHISPER_LOG_DEBUG(...) ((void)0)
struct whisper_token_data { int id, tid; };
struct Decoder {
    int seek_delta;
    struct { int result_len; float avg_logprobs; std::vector<whisper_token_data> tokens; } sequence;
    std::mt19937 rng{7};
};
struct whisper_context {};
struct whisper_state {
    std::vector<int> result_all;
    Decoder decoders[1];
    float no_speech_prob = 0.36f;
    int lang_id = 0;
};
struct whisper_full_params {
    int max_tokens = 0;
    bool single_segment = false;
    float no_speech_thold = 0.6f, logprob_thold = -1.0f;
    int test_windows = 1, test_start = 0, test_end = 3000;
};
int whisper_token_beg(whisper_context *) { return 1000; }
const char *whisper_lang_str(int id) { return id == 0 ? "en" : "da"; }
// Redirect only the compiled test's syscall, never the production trace path.
int trace_test_open(const char *path, int flags, mode_t mode) {
    if (std::strcmp(path, "/tmp/whisper-window-trace.jsonl") != 0) std::abort();
    return ::open(std::getenv("TRACE_TEST_PATH"), flags, mode);
}
#define open trace_test_open
"""

PROBES = r"""
int main(int argc, char **argv) {
    if (argc != 2) return 90;
    const std::string name = argv[1];
    whisper_context ctx; whisper_state state; whisper_full_params params;
    auto &decoder = state.decoders[0];
    decoder.seek_delta = 700;
    decoder.sequence.result_len = 2;
    decoder.sequence.avg_logprobs = -0.55f;
    decoder.sequence.tokens = {{42,1000},{1350,1349}};
    if (name == "double") decoder.sequence.tokens.push_back({1350,1349});
    if (name == "offset") { params.test_start = 126000; params.test_end = 129000; }
    if (name == "two") { params.test_windows = 2; params.test_end = 6000; }
    if (name == "zero") params.test_windows = 0;
    if (name == "silence") {
        state.no_speech_prob = 0.9f; decoder.sequence.avg_logprobs = -1.5f;
        decoder.sequence.tokens.clear(); decoder.seek_delta = 3000;
    }
    if (name == "eot") { decoder.sequence.tokens = {{999,1000}}; decoder.seek_delta = 3000; }
    if (name == "nan") state.no_speech_prob = NAN;
    if (name == "danish") state.lang_id = 1;
    if (name == "max_tokens") {
        params.max_tokens = 2; decoder.sequence.tokens.insert(decoder.sequence.tokens.begin(),{41,1000});
    }
    // Deliberately secret-looking strings must never reach trace output.
    const char *samples = "PRIVATE_TRANSCRIPT_SENTINEL PRIVATE_PROMPT_SENTINEL";
    const auto before = decoder;
    errno = EDOM;
    const int result = whisper_full_with_state(&ctx, &state, params,
                                               reinterpret_cast<const float *>(samples), 0);
    if (errno != EDOM || decoder.rng != before.rng || decoder.seek_delta != before.seek_delta ||
            decoder.sequence.result_len != before.sequence.result_len ||
            decoder.sequence.avg_logprobs != before.sequence.avg_logprobs ||
            decoder.sequence.tokens.size() != before.sequence.tokens.size()) return 91;
    for (size_t i = 0; i < decoder.sequence.tokens.size(); ++i) {
        if (decoder.sequence.tokens[i].id != before.sequence.tokens[i].id ||
                decoder.sequence.tokens[i].tid != before.sequence.tokens[i].tid) return 92;
    }
    std::printf("%d\n", result);
    return 0;
}
"""


def between(source, begin, end):
    return source.split(begin, 1)[1].split(end, 1)[0]


class NativeWindowTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++")
        if not compiler:
            raise RuntimeError("Native window trace regression requires C++17")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        original = FIXTURE.read_text()
        cls.executables = {}
        patched = PATCH.patch_window_trace(original)
        for name, source in (("upstream", original), ("patched", patched)):
            body = between(source, "// BEGIN REQUEST\n", "// END REQUEST")
            body += "\n int seek_start=params.test_start, seek=seek_start, seek_end=params.test_end;\n"
            body += " for(int window=0; window<params.test_windows; ++window) { int best_decoder_id=0;\n"
            body += between(source, "// BEGIN RESULT\n", "// END RESULT")
            body += between(source, "// BEGIN SEEK\n", "// END SEEK")
            body += "\n } return seek;\n}\n"
            cpp = cls.directory / (name + ".cpp")
            cpp.write_text(PREAMBLE + body + PROBES)
            executable = cls.directory / name
            subprocess.run([compiler, "-std=c++17", "-O2", str(cpp), "-o", str(executable)],
                           check=True, capture_output=True, timeout=60)
            cls.executables[name] = executable

    def setUp(self):
        self.output_dir = tempfile.TemporaryDirectory(dir=self.directory)
        self.addCleanup(self.output_dir.cleanup)
        self.output = Path(self.output_dir.name) / "trace.jsonl"

    def probe(self, case="early", *, enabled="1", variant="patched"):
        env = dict(os.environ, TRACE_TEST_PATH=str(self.output))
        env.pop("WHISPER_CPP_TRACE_WINDOWS", None)
        if enabled is not None:
            env["WHISPER_CPP_TRACE_WINDOWS"] = enabled
        result = subprocess.run([str(self.executables[variant]), case], env=env,
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stderr, "")
        return int(result.stdout)

    def lines(self):
        return [json.loads(line) for line in self.output.read_text().splitlines()]

    def test_fixture_is_exact_pinned_source_excerpts(self):
        self.assertEqual(sha256(FIXTURE.read_bytes()).hexdigest(),
                         "dbec6755822e2992f2f6a649e917800ad18c0c622b74b14eb801e588a8c35cfa")

    def test_missing_or_ambiguous_trace_anchor_is_refused(self):
        original = FIXTURE.read_text()
        for old, _new in PATCH.WINDOW_TRACE_PATCHES:
            for source in (original.replace(old, "", 1), original + old):
                with self.assertRaisesRegex(ValueError, "window trace anchor"):
                    PATCH.patch_window_trace(source)

    def test_disabled_default_and_non_exact_opt_in_do_not_create_trace(self):
        for setting in (None, "", "0", "true", "01", "1 "):
            self.assertEqual(self.probe(enabled=setting), 3000)
            self.assertFalse(self.output.exists())

    def test_early_final_timestamp_trace_exposes_unchanged_seek_override(self):
        self.assertEqual(self.probe(), 3000)
        line, = self.lines()
        self.assertEqual(line["seek_before_cs"], 0)
        self.assertEqual(line["seek_delta_before_cs"], 700)
        self.assertEqual(line["seek_delta_after_cs"], 3000)
        self.assertEqual(line["seek_after_cs"], 3000)
        self.assertEqual(line["last_timestamp_id_offset"], 350)
        self.assertEqual(line["last_timestamp_tid_offset"], 349)
        self.assertTrue(line["single_timestamp_ending"])
        self.assertFalse(line["is_no_speech"])
        self.assertAlmostEqual(line["no_speech_probability"], 0.36, places=6)
        self.assertEqual(line["language"], "en")

    def test_trace_is_observational_for_all_seek_cases(self):
        for case in ("early", "double", "offset", "two", "silence", "eot", "max_tokens"):
            self.assertEqual(self.probe(case), self.probe(case, variant="upstream"))

    def test_no_decode_means_no_line_and_each_actual_window_has_one_line(self):
        self.assertEqual(self.probe("zero"), 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.probe("two"), 6000)
        self.assertEqual([line["seek_before_cs"] for line in self.lines()], [0, 3000])

    def test_silence_eot_and_language_metadata_are_safe(self):
        self.probe("silence")
        self.probe("eot")
        self.probe("danish")
        self.probe("nan")
        silence, eot, danish, nonfinite = self.lines()
        self.assertTrue(silence["is_no_speech"])
        self.assertEqual(silence["last_timestamp_id_offset"], -1)
        self.assertEqual(eot["last_timestamp_tid_offset"], -1)
        self.assertEqual(danish["language"], "da")
        self.assertIsNone(nonfinite["no_speech_probability"])
        allowed = {"version", "seek_start_cs", "seek_end_cs", "seek_before_cs",
                   "seek_after_cs", "seek_delta_before_cs", "seek_delta_after_cs",
                   "result_len", "last_timestamp_id_offset", "last_timestamp_tid_offset",
                   "single_timestamp_ending", "max_tokens_timestamp_ending",
                   "is_no_speech", "no_speech_probability", "language"}
        for line in self.lines():
            self.assertEqual(set(line), allowed)
        self.assertNotIn("PRIVATE_", self.output.read_text())

    def test_trace_is_private_and_bounded_without_truncating_existing_data(self):
        self.probe()
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        original = b"x" * (LIMIT - 1)
        self.output.write_bytes(original)
        self.assertEqual(self.probe(), 3000)
        self.assertEqual(self.output.read_bytes(), original)

    def test_symlink_hardlink_and_insecure_modes_are_refused_without_affecting_decode(self):
        target = self.output.parent / "existing"
        target.write_bytes(b"untouched")
        target.chmod(0o600)
        self.output.symlink_to(target)
        self.assertEqual(self.probe(), 3000)
        self.assertEqual(target.read_bytes(), b"untouched")
        self.output.unlink()
        os.link(target, self.output)
        self.assertEqual(self.probe(), 3000)
        self.assertEqual(target.read_bytes(), b"untouched")
        self.output.unlink()
        self.output.write_bytes(b"untouched")
        self.output.chmod(0o644)
        self.assertEqual(self.probe(), 3000)
        self.assertEqual(self.output.read_bytes(), b"untouched")

    def test_fifo_and_directory_are_refused_without_blocking_or_changing_decode(self):
        os.mkfifo(self.output, 0o600)
        self.assertEqual(self.probe(), 3000)
        self.output.unlink()
        self.output.mkdir()
        self.assertEqual(self.probe(), 3000)

    def test_busy_trace_file_does_not_delay_inference(self):
        self.probe()
        before = self.output.read_bytes()
        with self.output.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.probe(), 3000)
        self.assertEqual(self.output.read_bytes(), before)

    def test_concurrent_append_cannot_exceed_size_limit(self):
        self.probe()
        line = self.output.read_bytes()
        prefix = b"x" * (LIMIT - len(line))
        self.output.write_bytes(prefix)
        env = dict(os.environ, TRACE_TEST_PATH=str(self.output), WHISPER_CPP_TRACE_WINDOWS="1")
        processes = [subprocess.Popen([str(self.executables["patched"]), "early"],
                                      env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(8)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual((process.returncode, stdout, stderr), (0, b"3000\n", b""))
        self.assertEqual(self.output.read_bytes(), prefix + line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
