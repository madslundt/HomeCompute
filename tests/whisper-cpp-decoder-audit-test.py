#!/usr/bin/env python3
"""Compile pinned completion/ranking/seek code; diagnostics must not alter it."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'decoder_patch', ROOT / 'deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
FIXTURE = ROOT / 'tests/fixtures/whisper-cpp-decoder-audit.cpp'
WINDOW = ROOT / 'tests/fixtures/whisper-cpp-window-trace.cpp'

PREAMBLE = r'''
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <map>
#include <random>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
#define WHISPER_LOG_DEBUG(...) ((void)0)
constexpr int WHISPER_CHUNK_SIZE = 30, WHISPER_MAX_DECODERS = 8;
using whisper_token = int;
struct whisper_token_data { int id, tid; double plog; };
struct whisper_sequence {
    int result_len = 0;
    double sum_logprobs_all = 0, sum_logprobs = -INFINITY;
    double avg_logprobs = -INFINITY, entropy = 0, score = -INFINITY;
    std::vector<whisper_token_data> tokens;
};
struct whisper_decoder {
    int seek_delta = 3000, grammar = 0;
    bool has_ts = false, failed = false, completed = false;
    whisper_sequence sequence;
    std::mt19937 rng{7};
};
struct whisper_context { struct { int n_loaded = 1; } model; };
struct whisper_state {
    std::vector<int> result_all;
    whisper_decoder decoders[8];
    float no_speech_prob = 0.36f;
    int lang_id = 0, n_fail_h = 0;
};
struct whisper_full_params {
    int max_tokens = 0;
    bool single_segment = false, no_timestamps = false;
    float no_speech_thold = 0.6f, logprob_thold = -1.0f;
    float length_penalty = -1.0f, entropy_thold = 0.0f;
    std::string test = "eot";
};
int whisper_token_beg(whisper_context *) { return 1000; }
int whisper_token_eot(whisper_context *) { return 999; }
const char *whisper_lang_str(int) { return "en"; }
void whisper_grammar_accept_token(whisper_context &, int &, int) {}
int audit_test_open(const char *path, int flags, mode_t mode) {
    if (std::strcmp(path, "/tmp/whisper-window-trace.jsonl") != 0) std::abort();
    return ::open(std::getenv("AUDIT_TEST_PATH"), flags, mode);
}
#define open audit_test_open
'''

SETUP = r'''
    int seek_start = 0, seek = 0, seek_end = 3000, delta_min = 10;
    std::vector<std::vector<whisper_token_data>> plans = {
        {{1000,1000,-.05},{41,1000,-.1},{1350,1350,-.05},{999,1000,-3}},
        {{1000,1000,-.05},{42,1000,-.4},{2000,2000,-.05},{999,1000,-.01}},
    };
    if (params.test == "shrinking") plans[1] = {
        {1000,1000,-.05},{42,1000,-.4},{1500,1500,-.05},{1500,1500,-.1},
        {43,1000,-.1},{2450,2450,-.05},{999,1000,-.01}};
    if (params.test == "unfinished") plans = {{
        {1000,1000,-.05},{41,1000,-.1},{2000,2000,-.05},{2000,2000,-.05},{42,1000,-.1}}};
    if (params.test == "audio_end") plans = {{
        {1000,1000,-.05},{41,1000,-.1},{2500,2500,-.05}}};
    if (params.test == "failure") plans = {{
        {1000,1000,-.05},{41,1000,-.1},{1350,1350,-.05},{1350,1350,-.05},
        {42,1000,-.1},{1300,1300,-.1}}};
    if (params.test == "max_tokens") params.max_tokens = 2;
    if (params.test == "nonfinite") plans[0].back().plog = -INFINITY;
    if (params.test == "silence") {
        state->no_speech_prob = .99;
        for (auto & plan : plans) for (auto & token : plan) token.plog = -2;
    }
    if (params.test == "eight") while (plans.size() < 8) plans.push_back(plans[1]);
    const int n_decoders = static_cast<int>(plans.size());
'''

LOOP_START = r'''
        const int attempts = params.test == "retry" ? 2 : 1;
        for (int it = 0; it < attempts; ++it) {
            int n_decoders_cur = n_decoders;
'''

DECODERS = r'''
            for (int j = 0; j < n_decoders_cur; ++j) state->decoders[j] = {};
            int steps = 0;
            for (const auto & plan : plans) steps = std::max(steps, static_cast<int>(plan.size()));
            for (int i = 0, n_max = steps; i < n_max; ++i) {
'''

SAMPLE = r'''
                for (int j = 0; j < n_decoders_cur; ++j) {
                    auto & decoder = state->decoders[j];
                    if (decoder.completed || decoder.failed) continue;
                    decoder.sequence.tokens.push_back(plans[j].at(i));
                    decoder.sequence.sum_logprobs_all += plans[j].at(i).plog;
                }
'''

PRINT = r'''
    std::printf("%d %d %d\n", best_decoder_id, seek, errno);
    for (int j = 0; j < n_decoders; ++j) {
        const auto & d = state->decoders[j];
        std::printf("%d %d %d %d %zu %.17g %.17g %.17g %lu\n", d.completed, d.failed,
                d.seek_delta, d.sequence.result_len, d.sequence.tokens.size(),
                d.sequence.sum_logprobs_all, d.sequence.score, d.sequence.avg_logprobs,
                static_cast<unsigned long>(std::mt19937(d.rng)()));
        for (auto token : d.sequence.tokens) std::printf("%d %.17g ", token.id, token.plog);
        std::puts("");
    }
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 90;
    whisper_context ctx; whisper_state state; whisper_full_params params;
    params.test = argv[1];
    errno = EDOM;
    return whisper_full_with_state(&ctx, &state, params, nullptr, 0);
}
'''


def between(source, section):
    return source.split('// BEGIN ' + section + '\n', 1)[1].split('// END ' + section, 1)[0]


class DecoderAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.directory = Path(cls.tmp.name)
        compiler = shutil.which('c++')
        if compiler is None:
            raise RuntimeError('Decoder diagnostic regression requires C++17')
        cls.original = WINDOW.read_text() + FIXTURE.read_text()
        patched = PATCH.patch_decoder_trace(PATCH.patch_window_trace(cls.original))
        cls.programs = {}
        for name, source in (('original', cls.original), ('patched', patched)):
            code = PREAMBLE + between(source, 'SCORE') + between(source, 'REQUEST') + SETUP
            code += between(source, 'INITIALIZE') + LOOP_START + between(source, 'COUNT') + DECODERS
            code += between(source, 'SAMPLE') + SAMPLE + between(source, 'UPDATE')
            code += between(source, 'COMPLETE') + '\n }\n' + between(source, 'RANK') + '\n }\n {\n'
            code += between(source, 'RESULT') + between(source, 'SEEK') + '\n }\n' + PRINT
            cpp = cls.directory / (name + '.cpp')
            cpp.write_text(code)
            binary = cls.directory / name
            result = subprocess.run([compiler, '-std=c++17', '-O2', str(cpp), '-o', str(binary)],
                                    check=False, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.programs[name] = binary

    def setUp(self):
        self.output = self.directory / 'trace.jsonl'
        self.output.unlink(missing_ok=True)

    def run_case(self, case='eot', enabled='1', variant='patched'):
        env = dict(os.environ, AUDIT_TEST_PATH=str(self.output))
        env.pop('WHISPER_CPP_TRACE_WINDOWS', None)
        if enabled is not None:
            env['WHISPER_CPP_TRACE_WINDOWS'] = enabled
        result = subprocess.run([self.programs[variant], case], env=env,
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stderr, '')
        return result.stdout

    def rows(self):
        return [json.loads(line) for line in self.output.read_text().splitlines()]

    def test_fixture_is_exact_reviewed_native_code(self):
        self.assertEqual(sha256(FIXTURE.read_bytes()).hexdigest(),
                         '84060dd40784ce315fcfc39ae5f98aa4767fce1ad6bd67cbbb5b380c81994fbe')

    def test_diagnostics_are_observational_for_actual_termination_rank_and_seek(self):
        for case in ('eot', 'shrinking', 'unfinished', 'audio_end', 'max_tokens',
                     'failure', 'silence', 'nonfinite', 'eight', 'retry'):
            with self.subTest(case=case):
                original = self.run_case(case, variant='original')
                self.assertEqual(self.run_case(case, enabled=None), original)
                self.assertEqual(self.run_case(case), original)

    def test_eot_score_difference_is_visible_without_reranking(self):
        output = self.run_case()
        self.assertTrue(output.startswith('0 3000 '))
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        a, b = rows[1:]
        self.assertEqual((a['raw_suffix'], a['retained_suffix']), ('TOTE', 'TOT'))
        self.assertEqual((a['pretrim_count'], a['result_len'], a['discarded_ordinary_count']), (4, 3, 0))
        self.assertEqual(a['termination'], 'eot')
        self.assertEqual(a['selected_decoder'], 0)
        self.assertEqual(a['terminal_eot_logprob'], -3)
        self.assertGreater(a['prefix_rank'], b['prefix_rank'])
        self.assertLess(a['complete_path_rank'], b['complete_path_rank'])

    def test_unfinished_suffix_does_not_masquerade_as_single_timestamp_ending(self):
        self.run_case('unfinished')
        window, decoder = self.rows()
        self.assertFalse(window['single_timestamp_ending'])
        self.assertEqual(decoder['termination'], 'decode_budget')
        self.assertEqual(decoder['discarded_ordinary_count'], 1)
        self.assertEqual(decoder['retained_suffix'][-2:], 'TT')
        self.assertIsNone(decoder['terminal_eot_logprob'])
        self.assertIsNone(decoder['complete_path_rank'])

    def test_active_search_shrinking_is_observed_at_each_beams_stop(self):
        self.run_case('shrinking')
        _, early, later = self.rows()
        self.assertEqual(early['active_beams_at_stop'], 2)
        self.assertEqual(later['active_beams_at_stop'], 1)
        self.assertEqual(early['active_beams_at_final_step'], 1)
        self.assertLess(early['stop_step'], later['stop_step'])

    def test_actual_limit_failure_and_audio_end_remain_distinct(self):
        for case, reason in (('max_tokens', 'max_tokens'), ('failure', 'failed'), ('audio_end', 'audio_end')):
            self.output.unlink(missing_ok=True)
            self.run_case(case)
            self.assertEqual(self.rows()[1]['termination'], reason)

    def test_only_final_attempt_is_emitted_and_eight_decoders_are_bounded(self):
        self.run_case('retry')
        self.assertEqual([row['attempt'] for row in self.rows()[1:]], [1, 1])
        self.output.unlink()
        self.run_case('eight')
        rows = self.rows()
        self.assertEqual(len(rows), 9)
        self.assertEqual([row['decoder_id'] for row in rows[1:]], list(range(8)))
        self.assertTrue(all(len(line) < 1024 for line in self.output.read_bytes().splitlines()))

    def test_trace_off_nonfinite_safety_and_no_raw_token_ids(self):
        self.run_case(enabled=None)
        self.assertFalse(self.output.exists())
        self.run_case('nonfinite')
        decoder = self.rows()[1]
        self.assertIsNone(decoder['complete_sum'])
        self.assertIsNone(decoder['terminal_eot_logprob'])
        self.assertIsNone(decoder['complete_path_rank'])
        for row in self.rows()[1:]:
            self.assertFalse({'tokens', 'token_ids', 'text', 'prompt'} & row.keys())
            self.assertLessEqual(set(row['raw_suffix'] + row['retained_suffix']), set('OTES'))

    def test_missing_ambiguous_anchors_and_unverified_helper_are_refused(self):
        source = PATCH.patch_window_trace(self.original)
        for old, _new in PATCH.DECODER_TRACE_PATCHES:
            for modified in (source.replace(old, '', 1), source + old):
                with self.assertRaisesRegex(ValueError, 'decoder trace anchor'):
                    PATCH.patch_decoder_trace(modified)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'patch-whisper-cpp-cuda.py'
            path.write_bytes((ROOT / 'deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py').read_bytes())
            path.with_name('whisper-cpp-trace-patch.py').write_text('raise RuntimeError("untrusted")')
            result = subprocess.run([os.sys.executable, path, '--help'], check=False,
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('trace helper checksum changed', result.stderr)
            self.assertNotIn('RuntimeError: untrusted', result.stderr)

    def test_reported_and_built_candidate_identity_matches_verified_patcher(self):
        directory = ROOT / 'deploy/whisper-cpp-stt'
        expected = sha256((directory / 'patch-whisper-cpp-cuda.py').read_bytes()).hexdigest()
        pin = json.loads((directory / 'model-pin.json').read_text())['cuda_candidate']['strict_gpu_patch']
        self.assertEqual(pin['patcher_sha256'], expected)
        self.assertEqual(pin['version'], PATCH.PATCH_VERSION)
        for path, version_name, hash_name in (
                (directory / 'adapter.py', 'CUDA_PATCH_VERSION', 'CUDA_PATCHER_SHA256'),
                (ROOT / 'scripts/setup-whisper-stt.py', 'CPP_CUDA_PATCH_VERSION', 'CPP_CUDA_PATCHER_SHA256')):
            values = {target.id: node.value.value for node in ast.parse(path.read_text()).body
                      if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                      for target in node.targets if isinstance(target, ast.Name)}
            self.assertEqual(values[version_name], PATCH.PATCH_VERSION, str(path))
            self.assertEqual(values[hash_name], expected, str(path))
        dockerfile = (directory / 'Dockerfile.cuda').read_text()
        self.assertEqual(dockerfile.count('ARG CUDA_PATCHER_SHA256=' + expected + '\n'), 2)
        self.assertEqual(dockerfile.count('io.homecompute.whisper-cpp.cuda-patch="' + PATCH.PATCH_VERSION + '"'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
