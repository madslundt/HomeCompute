#!/usr/bin/env python3
"""Compile pinned native sampling excerpts; no models, audio, network, or GPU."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cuda_patch", ROOT / "deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
FIXTURE = ROOT / "tests/fixtures/whisper-cpp-sampling.cpp"

PREAMBLE = r"""
#include <algorithm>
#include <cmath>
#include <random>
#include <string>
#include <type_traits>
#include <vector>
using whisper_token = int;
struct whisper_token_data {
    int id, tid; float p, plog, pt, ptsum; int t0, t1, t_dtw; float vlen;
};
struct whisper_context {
    struct { int n_vocab = 10, token_beg = 8; } vocab;
    struct { struct { int n_vocab = 10; } hparams; } model;
};
struct whisper_decoder {
    struct { std::vector<int> tokens; } sequence;
    std::vector<float> probs, logits, logprobs;
    std::vector<std::pair<float, int>> logits_id;
    mutable std::mt19937 rng{0};
};
struct whisper_state { whisper_decoder decoders[5]; };
"""

PROBES = r"""
void probabilities(whisper_decoder & d) {
    d.probs = {0.20f,0.18f,0.16f,0.14f,0.12f,0.10f,0.04f,0.03f,0.02f,0.01f};
    d.logits.clear(); d.logprobs.clear();
    for (auto p : d.probs) { d.logits.push_back(std::log(p)); d.logprobs.push_back(std::log(p)); }
}
std::vector<int> stochastic(whisper_context & ctx, whisper_decoder & d) {
    std::vector<int> result;
    for (int i = 0; i < 40; ++i) result.push_back(whisper_sample_token(ctx, d, false).id);
    return result;
}
int main(int argc, char ** argv) {
    if (argc != 2) return 99;
    const std::string test = argv[1];
    whisper_context ctx; whisper_state state;
    for (auto & d : state.decoders) probabilities(d);
    if (test == "topk") {
        auto initial_rng = state.decoders[0].rng;
        for (int call = 0; call < 3; ++call) {
            auto tokens = whisper_sample_token_topk(ctx, state.decoders[0], 5);
            for (int i = 0; i < 5; ++i) if (tokens[i].id != i) return 1;
        }
        return state.decoders[0].rng == initial_rng ? 0 : 2;
    }
    if (test == "ties") {
        auto & d = state.decoders[0];
        d.logits = {0,2,0,2,0,2,0,0,0,0};
        const auto tokens = whisper_sample_token_topk(ctx, d, 3);
        return tokens[0].id == 1 && tokens[1].id == 3 && tokens[2].id == 5 ? 0 : 1;
    }
    if (test == "timestamps") {
        auto & d = state.decoders[0];
        auto tokens = whisper_sample_token_topk(ctx, d, 5);
        for (auto t : tokens) {
            if (t.p != d.probs[t.id] || t.plog != d.logprobs[t.id] || t.tid != 8 ||
                std::fabs(t.pt - 2.0f/3) > 1e-6f || std::fabs(t.ptsum - 0.03f) > 1e-6f ||
                t.t0 != -1 || t.t1 != -1 || t.t_dtw != -1 || t.vlen != 0) return 1;
        }
        d.logits[9] = 10; d.logits[8] = 9;
        tokens = whisper_sample_token_topk(ctx, d, 2);
        for (int i = 0; i < 2; ++i) {
            auto t = tokens[i];
            if (t.id != 9-i || t.tid != t.id || t.pt != t.p) return 2;
        }
        return 0;
    }
    if (test == "request_rng") {
        // Same first-request sequence as a newly initialized native state.
        auto fresh = stochastic(ctx, state.decoders[0]);
        for (int request = 0; request < 3; ++request) {
            initialize_request(&ctx, &state, 5);
            for (int j = 0; j < 5; ++j) {
                whisper_decoder expected; probabilities(expected); expected.rng.seed(j);
                if (state.decoders[j].rng != expected.rng) return 3;
                if (stochastic(ctx, state.decoders[j]) != stochastic(ctx, expected)) return 1;
            }
            // An intervening request consumes a different number of random draws.
            for (int i = 0; i < 17 + request; ++i) state.decoders[0].rng();
        }
        initialize_request(&ctx, &state, 1);
        return stochastic(ctx, state.decoders[0]) == fresh ? 0 : 2;
    }
    return 98;
}
"""


class NativeSamplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++")
        if compiler is None:
            raise RuntimeError("Native decoder regression requires a C++17 compiler")
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.original = FIXTURE.read_text()
        cls.executables = {}
        for name, source in (("upstream", cls.original), ("patched", PATCH.patch_sampling(cls.original))):
            target = Path(cls.directory.name) / name
            cpp = target.with_suffix(".cpp")
            cpp.write_text(PREAMBLE + source + PROBES)
            subprocess.run([compiler, "-std=c++17", "-O2", str(cpp), "-o", str(target)],
                           check=True, capture_output=True, text=True, timeout=60)
            cls.executables[name] = target

    def probe(self, variant, name):
        return subprocess.run([str(self.executables[variant]), name],
                              capture_output=True, timeout=5).returncode

    def test_beam_topk_is_ranked_distinct_and_does_not_consume_rng(self):
        self.assertNotEqual(self.probe("upstream", "topk"), 0)
        self.assertEqual(self.probe("patched", "topk"), 0)

    def test_fixture_remains_the_verified_pinned_native_excerpt(self):
        self.assertEqual(sha256(FIXTURE.read_bytes()).hexdigest(),
                         "38edc332c7398b22a7a70ec116cf2118b87de0af98f219c2af69cf098af87388")

    def test_equal_logits_use_ascending_token_id(self):
        self.assertNotEqual(self.probe("upstream", "ties"), 0)
        self.assertEqual(self.probe("patched", "ties"), 0)

    def test_selected_tokens_keep_timestamp_probability_metadata(self):
        self.assertEqual(self.probe("patched", "timestamps"), 0)

    def test_stochastic_requests_are_fresh_process_equivalent_after_other_requests(self):
        self.assertNotEqual(self.probe("upstream", "request_rng"), 0)
        self.assertEqual(self.probe("patched", "request_rng"), 0)

    def test_missing_or_duplicated_sampling_anchor_is_refused(self):
        for old, _new in PATCH.SAMPLING_PATCHES:
            for source in (self.original.replace(old, "", 1), self.original + old):
                with self.assertRaisesRegex(ValueError, "sampling anchor"):
                    PATCH.patch_sampling(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
