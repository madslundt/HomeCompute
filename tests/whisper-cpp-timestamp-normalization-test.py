#!/usr/bin/env python3
"""Compile pinned filtering/sampling excerpts; no audio, model, GPU or network."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('timestamp_patch', ROOT / 'deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
FIXTURE = ROOT / 'tests/fixtures/whisper-cpp-timestamp-normalization.cpp'
SAMPLING = ROOT / 'tests/fixtures/whisper-cpp-sampling.cpp'

PREAMBLE = r'''
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <random>
#include <string>
#include <type_traits>
#include <vector>
using whisper_token = int;
struct whisper_token_data { int id, tid; float p,plog,pt,ptsum; int t0,t1,t_dtw; float vlen; };
struct whisper_context { struct { int n_vocab=6, token_beg=4; } vocab; };
struct whisper_decoder {
    std::vector<float> probs, logits, logprobs;
    std::vector<std::pair<float,int>> logits_id;
    mutable std::mt19937 rng{0}; int grammar=0;
};
struct whisper_full_params { int n_grammar_rules=0; };
int grammar_calls=0;
void whisper_suppress_invalid_grammar(whisper_context &, whisper_full_params,
        std::vector<float> & logits, int) {
    ++grammar_calls; logits[0]=-INFINITY;
}
'''

WRAPPER = r'''
void actual_filter(whisper_context & ctx, whisper_decoder & decoder, float temperature, int grammar) {
    const auto & vocab=ctx.vocab;
    const int n_logits=vocab.n_vocab;
    auto & logits=decoder.logits; auto & probs=decoder.probs; auto & logprobs=decoder.logprobs;
    probs.resize(n_logits); logprobs.resize(n_logits);
    whisper_full_params params; params.n_grammar_rules=grammar;
'''

PROBE = r'''
int main(int argc, char ** argv) {
    if (argc != 3) return 99;
    const std::string name=argv[1]; const float temperature=std::stof(argv[2]);
    std::vector<float> input;
    if (name=="force_a") input={.10,.10,.10,.10,.36,.24};
    else if (name=="force_b") input={.19,.19,.19,.19,.18,.06};
    else if (name=="unforced" || name=="grammar") input={.70,.05,.05,.05,.10,.05};
    else if (name=="no_timestamps") input={.70,.10,.10,.10,0,0};
    else if (name=="equal_mass") input={.5,0,0,0,.5,0};
    else if (name=="all_timestamp") input={0,0,0,0,.6,.4};
    else if (name=="all_masked") input={0,0,0,0,0,0};
    else return 98;
    whisper_context ctx; whisper_decoder d;
    for (auto p:input) d.logits.push_back(p>0 ? std::log(p) : -INFINITY);
    actual_filter(ctx,d,temperature,name=="grammar");
    std::cout << std::setprecision(12);
    if (name=="all_masked") {
        // Inspect filtering only: sampling from an empty distribution is out of scope.
        for (auto v:d.logits) std::cout << v << " "; std::cout << "\n";
        for (auto v:d.logprobs) std::cout << v << " "; std::cout << "\n";
        for (auto v:d.probs) std::cout << v << " "; std::cout << "\n";
        return 0;
    }
    bool forced=true;
    for(int i=0;i<ctx.vocab.token_beg;i++) if(d.logits[i]!=-INFINITY) forced=false;
    const auto top=whisper_sample_token_topk(ctx,d,2);
    const auto best=whisper_sample_token(ctx,d,true);
    const auto rng_after_topk=d.rng;
    std::discrete_distribution<> dist(d.probs.begin(),d.probs.end());
    const auto normalized=dist.probabilities();
    std::cout << forced << " " << grammar_calls << " " << std::accumulate(d.probs.begin(),d.probs.end(),0.0)
        << " " << top[0].id << " " << top[0].p << " " << top[0].plog << " " << top[0].ptsum
        << " " << normalized[top[0].id] << " " << best.id << " " << best.plog
        << " " << (rng_after_topk==std::mt19937(0)) << "\n";
    for (auto v:d.logits) std::cout << v << " "; std::cout << "\n";
    for (auto v:d.logprobs) std::cout << v << " "; std::cout << "\n";
    for (auto v:d.probs) std::cout << v << " "; std::cout << "\n";
    for (int i=0;i<64;i++) {
        auto t=whisper_sample_token(ctx,d,false);
        std::cout << t.id << " " << t.plog << " " << std::log(normalized[t.id]) << " ";
    }
    std::cout << "\n";
}
'''


def section(source, name):
    return source.split('// BEGIN ' + name + '\n', 1)[1].split('// END ' + name, 1)[0]


class TimestampNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        directory = Path(cls.tmp.name)
        compiler = shutil.which('c++')
        if not compiler:
            raise RuntimeError('Timestamp normalization regression requires C++17')
        cls.original = FIXTURE.read_text()
        treatment = PATCH.patch_timestamp_normalization
        cls.programs = {}
        samplers = PATCH.patch_sampling(SAMPLING.read_text()).split('void initialize_request(', 1)[0]
        for name, source in (('original', cls.original), ('normalized', treatment(cls.original))):
            code = PREAMBLE + section(source, 'PROBABILITIES') + WRAPPER
            code += section(source, 'TEMPERATURE') + '\n {\n' + section(source, 'TIMESTAMP_MASK') + '\n}\n'
            code += samplers + PROBE
            cpp = directory / (name + '.cpp'); binary = directory / name
            cpp.write_text(code)
            result = subprocess.run([compiler, '-std=c++17', '-O2', str(cpp), '-o', str(binary)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.programs[name] = binary

    def run_raw(self, name, temperature=0, variant='normalized'):
        result = subprocess.run([self.programs[variant], name, str(temperature)],
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stderr, '')
        return result.stdout

    def run_case(self, name, temperature=0, variant='normalized'):
        return [[float(v) for v in row.split()]
                for row in self.run_raw(name, temperature, variant).splitlines()]

    def test_forced_timestamp_mass_is_renormalized(self):
        for case, old_mass in (('force_a', .60), ('force_b', .24)):
            with self.subTest(case=case):
                self.assertAlmostEqual(self.run_case(case, variant='original')[0][2], old_mass, places=6)
                self.assertAlmostEqual(self.run_case(case)[0][2], 1, places=6)

    def test_actual_samplers_copy_conditional_probability_and_logprob(self):
        for case, expected in (('force_a', .60), ('force_b', .75)):
            with self.subTest(case=case):
                rows = self.run_case(case); summary = rows[0]
                self.assertEqual(summary[3], 4)
                self.assertAlmostEqual(summary[4], expected, places=6)
                self.assertAlmostEqual(summary[4], summary[7], places=6)
                self.assertAlmostEqual(summary[6], 1, places=6)
                self.assertEqual(summary[3], summary[8])
                self.assertEqual(summary[5], summary[9])
                self.assertEqual(summary[10], 1)
                for index in range(0, len(rows[4]), 3):
                    self.assertAlmostEqual(rows[4][index+1], rows[4][index+2], places=6)

    def test_cross_prefix_cumulative_ranking_can_reverse(self):
        original = [self.run_case(c, variant='original')[0][5] for c in ('force_a', 'force_b')]
        normalized = [self.run_case(c)[0][5] for c in ('force_a', 'force_b')]
        self.assertGreater(original[0], original[1])
        self.assertLess(normalized[0], normalized[1])

    def test_nonforcing_and_grammar_paths_remain_bit_identical(self):
        for case, temperature in (('unforced', 0), ('unforced', .5), ('grammar', 0),
                                  ('no_timestamps', 0), ('equal_mass', 0), ('force_b', .5)):
            with self.subTest(case=case, temperature=temperature):
                original = self.run_case(case, temperature, 'original')
                self.assertEqual(original[0][0], 0)
                self.assertEqual(self.run_raw(case, temperature), self.run_raw(case, temperature, 'original'))
        self.assertEqual(self.run_case('grammar')[0][1], 1)

    def test_temperature_is_still_applied_before_force_decision(self):
        self.assertEqual(self.run_case('force_b', 0)[0][0], 1)
        self.assertEqual(self.run_case('force_b', .5)[0][0], 0)
        for case in ('force_a', 'force_b', 'all_timestamp'):
            for temperature in (0, .5, 1):
                with self.subTest(case=case, temperature=temperature):
                    before = self.run_case(case, temperature, 'original')
                    after = self.run_case(case, temperature)
                    self.assertEqual(before[0][:2], after[0][:2])
                    self.assertEqual(before[1], after[1])  # Mask and scaled logits unchanged.
                    self.assertEqual(before[0][3], after[0][3])  # Same within-prefix top token.
                    self.assertEqual(before[4][::3], after[4][::3])  # Same synthetic sampled IDs.

    def test_probability_arrays_are_consistent_and_masked_text_stays_impossible(self):
        import math
        for case in ('force_a', 'force_b', 'all_timestamp'):
            rows = self.run_case(case)
            self.assertEqual(rows[3][:4], [0, 0, 0, 0])
            self.assertEqual(rows[2][:4], [-math.inf] * 4)
            for prob, logprob in zip(rows[3][4:], rows[2][4:]):
                self.assertAlmostEqual(prob, math.exp(logprob), places=6)

    def test_all_masked_distribution_does_not_enter_strict_timestamp_predicate(self):
        import math
        rows = self.run_case('all_masked')
        self.assertEqual(rows, [[-math.inf] * 6, [-math.inf] * 6, [0] * 6])
        self.assertEqual(self.run_raw('all_masked'), self.run_raw('all_masked', variant='original'))
        self.assertFalse(max(rows[1][4:]) > max(rows[1][:4]))

    def test_fixture_is_the_exact_pinned_native_excerpt(self):
        self.assertEqual(sha256(FIXTURE.read_bytes()).hexdigest(),
                         '0bf813c79e09fde30a43be3f3b50324b4539efa46e2e67b14329ea66764494b4')
        self.assertEqual(sha256(SAMPLING.read_bytes()).hexdigest(),
                         '38edc332c7398b22a7a70ec116cf2118b87de0af98f219c2af69cf098af87388')

    def test_missing_or_ambiguous_filter_anchor_is_refused(self):
        for old, _new in PATCH.TIMESTAMP_NORMALIZATION_PATCHES:
            for source in (self.original.replace(old, '', 1), self.original + old):
                with self.assertRaisesRegex(ValueError, 'timestamp normalization anchor'):
                    PATCH.patch_timestamp_normalization(source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
