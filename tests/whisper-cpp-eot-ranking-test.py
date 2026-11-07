#!/usr/bin/env python3
"""Compile the pinned native final-ranking seam against synthetic decoder states."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from hashlib import sha256

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('decoder_audit_tests', ROOT / 'tests/whisper-cpp-decoder-audit-test.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
PATCH = AUDIT.PATCH

SETUP = r'''
void whisper_write_window_trace(const char *, size_t) {}
int main(int argc, char ** argv) {
    if (argc != 2) return 90;
    const std::string test = argv[1];
    whisper_context context; auto * ctx = &context;
    whisper_state storage; auto * state = &storage;
    whisper_full_params params;
    const int n_decoders_cur = 2;
    int best_decoder_id = 0;
    const int seek = 0, seek_end = 3000, delta_min = 10;
    const int audit_step = 4, audit_active = 2;
    const bool trace_windows = std::getenv("WHISPER_CPP_TRACE_WINDOWS") != nullptr;
    whisper_decoder_audit decoder_audits[WHISPER_MAX_DECODERS];
    state->decoders[0].sequence.tokens = {
        {1000,1000,-.05},{41,1000,-.1},{1350,1350,-.05},{999,1000,-3}};
    state->decoders[1].sequence.tokens = {
        {1000,1000,-.05},{42,1000,-.4},{2000,2000,-.05},{999,1000,-.01}};
    for (auto & d : state->decoders) { d.completed = true; d.sequence.result_len = 3; }
    if (test == "non_eot") {
        for (auto & d : state->decoders) if (!d.sequence.tokens.empty()) d.sequence.tokens.pop_back();
    }
    if (test == "unfinished") for (auto & d : state->decoders) d.completed = false;
    if (test == "retained_eot") for (auto & d : state->decoders) d.sequence.result_len = 4;
    if (test == "discarded_suffix") for (auto & d : state->decoders) if (!d.sequence.tokens.empty())
        d.sequence.tokens.insert(d.sequence.tokens.end()-1, {43,1000,-.2});
    if (test == "failed") state->decoders[1].failed = true;
    if (test == "empty") {
        state->decoders[0].sequence.tokens = {{999,1000,-.01}};
        state->decoders[0].sequence.result_len = 0;
    }
    if (test == "tie") state->decoders[1].sequence.tokens = state->decoders[0].sequence.tokens;
    if (test == "nonfinite") state->decoders[0].sequence.tokens.back().plog = -INFINITY;
    if (test == "alpha_default" || test == "alpha_zero" || test == "alpha_positive") {
        state->decoders[0].sequence.tokens = {
            {1000,1000,-.1},{41,1000,-.3},{1350,1350,-.1},{999,1000,-.2}};
        state->decoders[1].sequence.tokens = {
            {1000,1000,-.1},{42,1000,-.2},{43,1000,-.3},{44,1000,-.2},
            {2000,2000,-.1},{999,1000,-.1}};
        state->decoders[1].sequence.result_len = 5;
        if (test == "alpha_zero") params.length_penalty = 0;
        if (test == "alpha_positive") params.length_penalty = 1;
    }
    if (test == "entropy_failure") {
        params.entropy_thold = .5;
        auto & d = state->decoders[1];
        d.sequence.tokens.assign(35, {42,1000,-.01});
        d.sequence.tokens.push_back({999,1000,-.01});
        d.sequence.result_len = 35;
    }
    for (auto & d : state->decoders)
        for (const auto & t : d.sequence.tokens) d.sequence.sum_logprobs_all += t.plog;
'''

OUTPUT = r'''
    std::printf("%d %d\n", best_decoder_id, state->n_fail_h);
    for (int j = 0; j < n_decoders_cur; ++j) {
        const auto & d = state->decoders[j];
        std::printf("%d %d %d %zu %.17g %.17g %.17g %.17g %.17g %lu\n",
            d.completed, d.failed, d.sequence.result_len, d.sequence.tokens.size(),
            d.sequence.sum_logprobs_all, d.sequence.sum_logprobs, d.sequence.avg_logprobs,
            d.sequence.score, d.sequence.entropy, static_cast<unsigned long>(std::mt19937(d.rng)()));
        for (const auto & t : d.sequence.tokens) std::printf("%d %.17g ", t.id, t.plog);
        std::puts("");
    }
}
'''

CASES = ('rank_reversal', 'non_eot', 'unfinished', 'retained_eot', 'discarded_suffix',
         'failed', 'empty', 'tie', 'nonfinite', 'alpha_default', 'alpha_zero',
         'alpha_positive', 'entropy_failure')


class CompleteEotRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.directory = Path(cls.tmp.name)
        compiler = shutil.which('c++')
        if compiler is None:
            raise RuntimeError('EOT ranking regression requires C++17')
        cls.original = AUDIT.WINDOW.read_text() + AUDIT.FIXTURE.read_text()
        treatment = PATCH.patch_complete_eot_ranking
        cls.programs = {}
        for name, source in (
                ('original', cls.original), ('ranked', treatment(cls.original)),
                ('traced', treatment(PATCH.patch_decoder_trace(PATCH.patch_window_trace(cls.original))))):
            code = AUDIT.PREAMBLE + PATCH.trace_namespace['DECODER_HELPERS']
            code += AUDIT.between(source, 'SCORE') + SETUP + AUDIT.between(source, 'RANK') + OUTPUT
            cpp = cls.directory / (name + '.cpp')
            cpp.write_text(code)
            binary = cls.directory / name
            result = subprocess.run([compiler, '-std=c++17', '-O2', str(cpp), '-o', str(binary)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.programs[name] = binary

    def run_case(self, case, variant='ranked', trace=False):
        env = dict(os.environ)
        env.pop('WHISPER_CPP_TRACE_WINDOWS', None)
        if trace:
            env['WHISPER_CPP_TRACE_WINDOWS'] = '1'
        result = subprocess.run([self.programs[variant], case], env=env,
                                capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(result.stderr, '')
        return result.stdout.splitlines()

    def test_genuine_omitted_eot_changes_only_winner(self):
        original = self.run_case('rank_reversal', 'original')
        ranked = self.run_case('rank_reversal')
        self.assertEqual(original[0], '0 0')  # Old scoring favors the cheap prefix.
        self.assertEqual(ranked[0], '1 0')  # Its expensive actual EOT now counts.
        self.assertEqual(ranked[1:], original[1:])

    def test_trace_is_not_required_and_cannot_change_treatment(self):
        for case in CASES:
            with self.subTest(case=case):
                expected = self.run_case(case)
                self.assertEqual(self.run_case(case, 'traced', False), expected)
                self.assertEqual(self.run_case(case, 'traced', True), expected)

    def test_prefix_confidence_entropy_tokens_rng_and_fallback_inputs_unchanged(self):
        for case in CASES:
            with self.subTest(case=case):
                self.assertEqual(self.run_case(case)[1:], self.run_case(case, 'original')[1:])

    def test_non_eot_unfinished_and_other_trimming_are_unchanged(self):
        for case in ('non_eot', 'unfinished', 'retained_eot', 'discarded_suffix'):
            with self.subTest(case=case):
                self.assertEqual(self.run_case(case), self.run_case(case, 'original'))

    def test_failed_zero_length_and_entropy_guards_are_retained(self):
        for case in ('failed', 'empty', 'entropy_failure'):
            with self.subTest(case=case):
                self.assertEqual(self.run_case(case), self.run_case(case, 'original'))
        self.assertEqual(self.run_case('entropy_failure')[0], '0 1')

    def test_strict_tie_keeps_lower_decoder_id(self):
        self.assertEqual(self.run_case('tie')[0], '0 0')

    def test_native_length_penalty_semantics_include_zero_as_length_normalization(self):
        self.assertEqual(self.run_case('alpha_default')[0], '1 0')
        self.assertEqual(self.run_case('alpha_zero')[0], '1 0')
        self.assertEqual(self.run_case('alpha_positive')[0], '0 0')
        self.assertEqual(self.run_case('alpha_default', 'original')[0], '0 0')

    def test_impossible_eot_does_not_gain_a_finite_prefix_score(self):
        self.assertEqual(self.run_case('nonfinite')[0], '1 0')
        self.assertEqual(self.run_case('nonfinite', 'original')[0], '0 0')

    def test_actual_upstream_fixture_is_unchanged(self):
        self.assertEqual(sha256(AUDIT.FIXTURE.read_bytes()).hexdigest(),
                         '84060dd40784ce315fcfc39ae5f98aa4767fce1ad6bd67cbbb5b380c81994fbe')

    def test_missing_or_ambiguous_native_anchors_are_refused(self):
        for old, _new in PATCH.EOT_RANKING_PATCHES:
            for source in (self.original.replace(old, '', 1), self.original + old):
                with self.assertRaisesRegex(ValueError, 'EOT ranking anchor'):
                    PATCH.patch_complete_eot_ranking(source)


class RankedRecoveryIsolationTests(unittest.TestCase):
    """Keep the controlled h17 comparison restricted to the reviewed objective."""

    def test_exact_reviewed_v7_native_patcher_is_used(self):
        self.assertEqual(sha256((ROOT / 'deploy/whisper-cpp-stt/patch-whisper-cpp-cuda.py').read_bytes()).hexdigest(),
                         'f6e4c5a2eb6bc2db7c2e5f7c76de3f2a2ef8b1741a2a1a1288ee3e3746a20551')

    def test_h17_recovery_language_non_speech_detector_and_contract_bytes_are_preserved(self):
        expected = {
            'deploy/whisper-cpp-stt/contract.py':
                '69924240fb6bfd274a58e1a091273da2617cfa976c9dfdcd24b36668239c3327',
            'deploy/whisper-cpp-stt/verify_model.py':
                '58c8674c2adb4a84ee6a1f0b8988ab403c79f7c458dcabc8c32eed666c50007f',
            'deploy/whisper-cpp-stt/recovery.py':
                'e5522d00810e62031f2f327cf6a29710e85d315b2ce51f536b1a7e5ac6feb7b3',
            'deploy/whisper-cpp-stt/silero_vad.py':
                'ca3be7ec09af7cf7f029c19a390e810e838c1bb197f363dd420eec1619f19ee7',
            'deploy/whisper-cpp-stt/language_boundaries.py':
                'b1bb5e927d435bf778059b0a557518ee5c2bafff0105c82c114a4454154682e1',
            'deploy/whisper-cpp-stt/non_speech.py':
                '68d37c6c27dfdde23a414c7899f487a55994a2f7726ec2fbaf8a4ef7f456cf9d',
            'deploy/whisper-cpp-stt/vad-pin.json':
                '2226f4f2781e786584d40cd13f12325b3f5477f5544232e9b6f94923916eb8b2',
            'deploy/whisper-cpp-stt/whisper-cpp-trace-patch.py':
                '244bfc3fcd3bef4c6cf1dfe0fc98a69aaab98fb1626c2a8c339d0a12d975bf04',
        }
        for name, digest in expected.items():
            with self.subTest(path=name):
                self.assertEqual(sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_adapter_lifecycle_and_build_metadata_are_the_only_other_runtime_differences(self):
        expected = {
            'deploy/whisper-cpp-stt/adapter.py':
                '6be47269769338132a5aa1b0dbf945d9b249c908731273417cd8d0f6f143a818',
            'deploy/whisper-cpp-stt/Dockerfile.cuda':
                'd1054d7b7dc2bfa5a7832f626cc0790d473558f8cdab97c5947544b9ac3d06ce',
            'deploy/whisper-cpp-stt/model-pin.json':
                'af2544bb66aaf4560e521c04d7583b87ce4a79390fbd9fb9c63f88c3d595c4de',
            'deploy/whisper-cpp-stt/compose-cuda.yaml':
                'e569836267d4f65af55b76ed24be1b3245bf79cd3191527d78143f08bf1831d9',
            'scripts/setup-whisper-stt.py':
                'd0974c43cd2d98eb84af489c11c12a3ace095bb4973f76b0752638ca1fcb2a17',
        }
        for name, digest in expected.items():
            source = (ROOT / name).read_text()
            source = source.replace('strict-gpu-complete-eot-ranking-v7', 'strict-gpu-decoder-audit-v6')
            source = source.replace('f6e4c5a2eb6bc2db7c2e5f7c76de3f2a2ef8b1741a2a1a1288ee3e3746a20551',
                                    '8639875ec008a0ac440887b837c5c0a5766e70fb03d8f9c728ab5860a7bd60a5')
            source = source.replace('cuda121a-ranked-recovery', 'cuda121a-hc10')
            with self.subTest(path=name):
                self.assertEqual(sha256(source.encode()).hexdigest(), digest)

    def test_cached_native_image_and_detector_have_separate_exact_provenance(self):
        dockerfile = (ROOT / 'deploy/whisper-cpp-stt/Dockerfile.recovery').read_text()
        self.assertIn('FROM sha256:dd7c842ebe50ba85773d21653cb1c29353b622848cfa709140123d5d375a2f22\n', dockerfile)
        self.assertIn('FROM sha256:84abcd6b3489162a6e479c29411acbb68598b9a4f48096e3372ee64b1aa6d8d4 AS detector\n', dockerfile)
        self.assertIn('native-source-revision="b440dae9d55d2419c72520dcd098d980ea30ba41"', dockerfile)
        self.assertIn('COPY --from=detector /opt/homecompute/ggml-silero-v5.1.2.bin /opt/homecompute/', dockerfile)
        self.assertNotIn('ARG NATIVE_BASE_IMAGE', dockerfile)
        self.assertNotIn('COPY --from=build', dockerfile)
        self.assertNotIn('cmake', dockerfile)
        self.assertNotIn('apt-get', dockerfile)
        self.assertIn('WHISPER_CPP_RECOVER_TRAILING_SPEECH=true', dockerfile)
        self.assertIn('WHISPER_CPP_LANGUAGE_BOUNDARIES=true', dockerfile)
        self.assertIn('WHISPER_CPP_FILTER_NON_SPEECH=true', dockerfile)


if __name__ == '__main__':
    unittest.main(verbosity=2)
