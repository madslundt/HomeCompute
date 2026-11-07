"""Language probes select acoustic geometry; hypothesis text never selects it."""
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('language_window_adapter', ROOT/'deploy/whisper-cpp-stt/adapter.py')
A = importlib.util.module_from_spec(spec);sys.modules[spec.name] = A;spec.loader.exec_module(A)
R = A._RECOVERY_MODULE


def audio(seconds=8, value=1):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes(value.to_bytes(2,'little',signed=True)*round(seconds*16000))
    return stream.getvalue()


def result(text='', language='en', duration=4, probability=.9):
    return json.dumps({'text':text+'\n' if text else '', 'segments':[
        {'text':text, 'start':0, 'end':duration}] if text else [],
        'duration':duration, 'task':'transcribe', 'language':'english' if language=='en' else 'danish',
        'language_selection':{'mode':'candidate_auto', 'candidates':['da','en'],
                              'selected':language, 'selected_probability':probability,
                              'detected':language, 'detected_probability':probability}}).encode()


class LanguageWindowTests(unittest.TestCase):
    def execute(self, replies, *, candidates=True):
        fields=[('response_format','verbose_json'),('language','auto')]
        if candidates:fields.append(('language_candidates','da,en'))
        children=[(0,4,audio(4)),(4,4,audio(4))]
        with patch.object(R._BOUNDARIES,'partitions',return_value=children), \
                patch.object(A,'_native_request',side_effect=replies) as request:
            body,_=A.transcribe_audio(audio(),8,'verbose_json',fields,10,
                recover_trailing_speech=True,language_boundaries=True)
        return json.loads(body),request

    def test_same_language_keeps_full_context_despite_possible_short_context_omissions(self):
        body,request=self.execute([result(),result(probability=.1),result(' Complete original',duration=8)])
        self.assertEqual(body['text'],' Complete original\n')
        self.assertNotIn('language_boundary_recovery',body)
        self.assertEqual(request.call_count,3)
        for call in request.call_args_list[:2]:
            # detect_language is deliberately native-only, never a public field.
            with patch.object(A._CODEC_MODULE,'ALLOWED_FIELDS',A._CODEC_MODULE.ALLOWED_FIELDS|{'detect_language'}):
                fields,child=A.parse_multipart(call.args[0],call.args[1])
            self.assertEqual(fields['detect_language'],'true')
            self.assertEqual(child,audio(4))
        fields,source=A.parse_multipart(*request.call_args.args[:2])
        self.assertNotIn('detect_language',fields);self.assertEqual(source,audio())

    def test_confident_change_decodes_every_child_once_and_keeps_both_languages(self):
        body,request=self.execute([result(language='da'),result(),
                                  result(' Første',language='da'),result(' Second')])
        self.assertEqual(body['text'],' Første\n Second\n')
        self.assertEqual([(s['start'],s['end']) for s in body['segments']],[(0,4),(4,8)])
        audit=body['language_boundary_recovery']
        self.assertEqual(audit['native_calls'],4);self.assertEqual(audit['language_probes'],2)
        self.assertEqual([p['source_region_start_frame'] for p in audit['regions']],[0,64000])
        self.assertEqual([p['decoded_windows'][0]['asr_language_windows'][0]['language_selection']['selected']
                          for p in audit['regions']],['da','en'])
        self.assertFalse(audit['coverage_claim'])
        for call in request.call_args_list[2:]:
            fields,source=A.parse_multipart(*call.args[:2])
            self.assertNotIn('detect_language',fields);self.assertEqual(source,audio(4))

    def test_malformed_or_failed_probe_never_publishes_a_partial_success(self):
        for bad in (b'{}',result(' unexpected text'),A.RequestError(502,'fixed')):
            with self.subTest(bad=bad),self.assertRaises(A.RequestError):
                self.execute([result(language='da'),bad])

    def test_no_candidate_profile_does_not_probe_or_partition(self):
        body,request=self.execute([result(' Original',duration=8)],candidates=False)
        self.assertEqual(request.call_count,1);self.assertEqual(body['text'],' Original\n')

    def test_detector_failure_is_not_a_silent_fallback_to_known_partial_output(self):
        with patch.object(R._BOUNDARIES,'partitions',side_effect=ValueError('synthetic')), \
                patch.object(A,'_native_request') as request,self.assertRaises(A.RequestError):
            A.transcribe_audio(audio(),8,'verbose_json',[('language_candidates','da,en')],10,
                recover_trailing_speech=True,language_boundaries=True)
        request.assert_not_called()

    def test_explicit_language_takes_precedence_without_automatic_probes(self):
        with patch.object(R._BOUNDARIES,'partitions',side_effect=AssertionError('explicit')), \
                patch.object(A,'_native_request',return_value=result(' Original',duration=8)) as request:
            A.transcribe_audio(audio(),8,'verbose_json',
                [('language_candidates','da,en'),('language','da')],10,
                recover_trailing_speech=True,language_boundaries=True)
        self.assertEqual(request.call_count,1)

    def test_explicit_translation_keeps_existing_behavior_without_probes(self):
        with patch.object(R._BOUNDARIES,'partitions',side_effect=AssertionError('translation')), \
                patch.object(A,'_native_request',return_value=result(' Original',duration=8)) as request:
            A.transcribe_audio(audio(),8,'verbose_json',
                [('language_candidates','da,en'),('translate','true')],10,
                recover_trailing_speech=True,language_boundaries=True)
        self.assertEqual(request.call_count,1)

    def test_probes_and_asr_share_one_decreasing_deadline(self):
        ticks=itertools.count()
        with patch.object(R._BOUNDARIES,'partitions',return_value=[(0,4,audio(4)),(4,4,audio(4))]), \
                patch.object(A,'_native_request',side_effect=[result(),result(),result(' Complete',duration=8)]) as request:
            R.transcribe(audio(),8,[('language_candidates','da,en')],100,
                request=A._native_request,codec=A._CODEC_MODULE,clock=lambda:next(ticks),language_boundaries=True)
        self.assertEqual([call.args[2] for call in request.call_args_list],[99,97,95])

    def test_veto_merges_original_pcm_before_asr_and_uses_one_deadline(self):
        proposals = [(offset, 4, audio(4)) for offset in (0, 4, 8)]
        replies = [result(language=lang) for lang in ('da', 'en', 'da')]
        replies.append(result(' Complete', language='da', duration=12))
        ticks = itertools.count()
        observations = []

        def observe(chunks, remaining):
            observations.append((chunks, remaining))
            return ['da']

        with patch.object(R._BOUNDARIES, 'partitions', return_value=proposals), \
                patch.object(A, '_native_request', side_effect=replies) as request:
            raw, _ = R.transcribe(audio(12), 12, [('language_candidates', 'da,en')], 100,
                                  request=A._native_request, codec=A._CODEC_MODULE, clock=lambda: next(ticks),
                                  language_boundaries=True, language_observer=observe)
        self.assertEqual(observations, [((audio(4),), 93)])
        self.assertEqual(request.call_count, 4)  # Three original LID probes and one merged decode.
        fields, data = A.parse_multipart(*request.call_args.args[:2])
        self.assertNotIn('language', fields)
        self.assertEqual(data, audio(12))
        value = json.loads(raw)
        self.assertEqual(value['text'], ' Complete\n')
        self.assertTrue(value['independent_language_boundaries']['islands'][0]['vetoed'])
        recovery = value['language_boundary_recovery']
        self.assertEqual(recovery['window_reflow'], 'minimum-original-pcm-context-reflow-v1')
        self.assertEqual([[(w['source_start_frame'], w['frames']) for w in r['decoded_windows']]
                          for r in recovery['regions']], [[(0, 192000)]])

    def test_expired_independent_observation_prevents_asr(self):
        proposals = [(offset, 4, audio(4)) for offset in (0, 4, 8)]
        ticks = itertools.count()

        def expired(_chunks, _remaining):
            for _ in range(20):
                next(ticks)
            return ['da']

        with patch.object(R._BOUNDARIES, 'partitions', return_value=proposals), \
                patch.object(A, '_native_request', side_effect=[result(language=lang)
                             for lang in ('da', 'en', 'da')]) as request, self.assertRaises(A.RequestError):
            R.transcribe(audio(12), 12, [('language_candidates', 'da,en')], 20, request=A._native_request,
                         codec=A._CODEC_MODULE, clock=lambda: next(ticks), language_boundaries=True,
                         language_observer=expired)
        self.assertEqual(request.call_count, 3)

    def test_long_combination_retains_actual_child_provenance_at_global_offsets(self):
        children=[(index*8,8,audio(8)) for index in range(4)]
        replies=[result(duration=8),result(duration=8),
                 result(language='da',duration=8),result(duration=8),result(' First window',duration=16),
                 result(' Anden',language='da',duration=8),result(' Final',duration=8)]
        with patch.object(R._BOUNDARIES,'partitions',return_value=children), \
                patch.object(A,'_native_request',side_effect=replies):
            raw,_=A.transcribe_audio(audio(32),32,'verbose_json',[('language_candidates','da,en')],10,
                recover_trailing_speech=True,language_boundaries=True)
        value=json.loads(raw);audit=value['language_boundary_recovery']['regions']
        self.assertEqual(audit[1]['source_region_start_frame'],256000)
        actual=[region['decoded_windows'][0]['asr_language_windows'][0] for region in audit[1:]]
        self.assertEqual([(item['start'],item['end']) for item in actual],[(16,24),(24,32)])
        self.assertEqual([item['language_selection']['selected'] for item in actual],['da','en'])

    def test_language_regions_cross_old_fixed_boundaries_before_asr_is_bounded(self):
        proposals=[(0,20,audio(20)),(20,20,audio(20)),(40,13,audio(13))]
        replies=[result(duration=20),result(duration=20),result(language='da',duration=13),
                 result(' English first',duration=30),result(' English rest',duration=10),
                 result(' Dansk',language='da',duration=13)]
        with patch.object(R._BOUNDARIES,'partitions',return_value=proposals), \
                patch.object(A,'_native_request',side_effect=replies) as request:
            raw,_=A.transcribe_audio(audio(53),53,'verbose_json',[('language_candidates','da,en')],10,
                recover_trailing_speech=True,language_boundaries=True)
        for call,seconds in zip(request.call_args_list[3:],[30,10,13]):
            fields,data=A.parse_multipart(*call.args[:2])
            self.assertNotIn('detect_language',fields);self.assertEqual(data,audio(seconds))
        audit=json.loads(raw)['language_boundary_recovery']['regions']
        self.assertEqual([(x['source_region_start_frame'],x['source_region_frames']) for x in audit],[(0,640000),(640000,208000)])
        self.assertEqual([x['frames'] for x in audit[0]['decoded_windows']],[480000,160000])

    def test_independent_observer_is_operator_only_and_requires_existing_policies(self):
        env = {'WHISPER_CPP_HOME_COMPUTE_REVISION': '1' * 40, 'WHISPER_CPP_IMAGE_ID': 'sha256:' + '2' * 64,
               'WHISPER_CPP_DEVICE': 'cuda', 'WHISPER_CPP_RECOVER_TRAILING_SPEECH': 'true',
               'WHISPER_CPP_LANGUAGE_BOUNDARIES': 'true'}
        with patch.dict(os.environ, env, clear=True), patch.object(R, 'verify_detector'):
            self.assertIsNone(A.load_config().language_observer)
        for changes in ({'WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS': 'yes'},
                        {'WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS': 'true'},
                        {'WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS': 'true',
                         'WHISPER_CPP_LANGUAGE_BOUNDARIES': 'false', 'WHISPER_CPP_LOCAL_EOT_RANKING': 'true'}):
            with self.subTest(changes=changes), patch.dict(os.environ, {**env, **changes}, clear=True), \
                    self.assertRaises(ValueError):
                A.load_config()
        for field in ('verify_language_islands', 'observer_manifest_sha256'):
            with self.subTest(field=field), self.assertRaises(A.RequestError):
                A.normalize_fields({field: 'true'})

    def test_operator_bundle_preflight_is_forwarded_through_real_adapter_veto(self):
        code = ('common.py', 'graph.py', 'export.py', 'classify.py', 'installer_check.py',
                'model.json', 'dependencies.json', 'requirements.lock')
        pin = {'source_revision': '3' * 40, 'code_sha256': {name: '4' * 64 for name in code},
               'manifest_sha256': '5' * 64, 'parity_sha256': '6' * 64,
               'approved_warning_sha256': '7' * 64}
        pin_raw = json.dumps(pin).encode()
        env = {'WHISPER_CPP_HOME_COMPUTE_REVISION': '1' * 40,
               'WHISPER_CPP_IMAGE_ID': 'sha256:' + '2' * 64, 'WHISPER_CPP_DEVICE': 'cuda',
               'WHISPER_CPP_RECOVER_TRAILING_SPEECH': 'true', 'WHISPER_CPP_LANGUAGE_BOUNDARIES': 'true',
               'WHISPER_CPP_FILTER_NON_SPEECH': 'true', 'WHISPER_CPP_LOCAL_EOT_RANKING': 'true',
               'WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS': 'true'}
        observer = Mock(return_value=['da'])
        bundle_factory = Mock(side_effect=lambda **values: SimpleNamespace(**values))
        def factory(bundle):
            observer.bundle = bundle
            return observer
        fake_module = SimpleNamespace(ObserverBundle=bundle_factory, LanguageObserver=Mock(side_effect=factory))
        module_name = 'homecompute_whisper_language_observer'
        fake_spec = SimpleNamespace(name=module_name, loader=SimpleNamespace(exec_module=Mock()))
        real_stat, real_read = Path.stat, Path.read_bytes
        def stat(path, *args, **kwargs):
            return (SimpleNamespace(st_size=len(pin_raw)) if path.name == 'language-observer-pin.json'
                    else real_stat(path, *args, **kwargs))
        def read(path):
            return pin_raw if path.name == 'language-observer-pin.json' else real_read(path)
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules), \
                patch.object(A.importlib.util, 'spec_from_file_location', return_value=fake_spec), \
                patch.object(A.importlib.util, 'module_from_spec', return_value=fake_module), \
                patch.object(Path, 'stat', stat), patch.object(Path, 'read_bytes', read), \
                patch.object(R, 'verify_detector'), patch.object(R._VAD, 'speech_spans', return_value=[]):
            config = A.load_config()
        bundle_factory.assert_called_once_with(source_directory=Path('/opt/homecompute/language-observer'),
                                               graph_directory=Path('/models/language-observer'),
                                               parity_report=Path('/models/language-observer/parity.json'), **pin)
        fake_module.LanguageObserver.assert_called_once_with(observer.bundle)
        observer.preflight.assert_called_once_with(30)
        self.assertIs(config.language_observer, observer)
        proposals = [(offset, 4, audio(4, value)) for offset, value in ((0, 1), (4, 2), (8, 3))]
        pcm = b''.join(R._BOUNDARIES._read_pcm(data)[1] for _, _, data in proposals)
        original = R._BOUNDARIES._wav(pcm)
        replies = [result(language=language) for language in ('da', 'en', 'da')]
        replies.append(result(' Complete', language='da', duration=12))
        with patch.object(R._BOUNDARIES, 'partitions', return_value=proposals), \
                patch.object(R._VAD, 'speech_spans', return_value=[]), \
                patch.object(A, '_native_request', side_effect=replies) as request:
            raw, _ = A.transcribe_audio(original, 12, 'verbose_json', [('language_candidates', 'da,en')],
                                       config.native_timeout_seconds,
                                       recover_trailing_speech=config.recover_trailing_speech,
                                       language_boundaries=config.language_boundaries,
                                       filter_non_speech=config.filter_non_speech,
                                       local_eot_ranking=config.local_eot_ranking,
                                       language_observer=config.language_observer)
        observer.assert_called_once()
        inputs, remaining = observer.call_args.args
        self.assertEqual(inputs, (proposals[1][2],))
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, config.native_timeout_seconds)
        self.assertEqual(request.call_count, 4)
        with patch.object(A._CODEC_MODULE, 'ALLOWED_FIELDS',
                          A._CODEC_MODULE.ALLOWED_FIELDS | {'homecompute_eot_spans'}):
            fields, submitted = A.parse_multipart(*request.call_args.args[:2])
        self.assertEqual(submitted, original)
        self.assertNotIn('language', fields)
        self.assertNotIn('detect_language', fields)
        self.assertEqual(fields['homecompute_eot_spans'], '')
        value = json.loads(raw)
        self.assertEqual(value['text'], ' Complete\n')
        self.assertTrue(value['independent_language_boundaries']['islands'][0]['vetoed'])

    def test_enabled_whole_source_policy_cannot_advertise_unsupported_duration(self):
        env={'WHISPER_CPP_HOME_COMPUTE_REVISION':'1'*40,'WHISPER_CPP_IMAGE_ID':'sha256:'+'2'*64,
             'WHISPER_CPP_DEVICE':'cuda','WHISPER_CPP_RECOVER_TRAILING_SPEECH':'true',
             'WHISPER_CPP_LANGUAGE_BOUNDARIES':'true','WHISPER_CPP_MAX_UPLOAD_BYTES':'40000000'}
        with patch.dict(os.environ,{**env,'WHISPER_CPP_MAX_AUDIO_SECONDS':'1201'},clear=True), \
                self.assertRaises(ValueError):
            A.load_config()
        with patch.dict(os.environ,{**env,'WHISPER_CPP_MAX_AUDIO_SECONDS':'1200'},clear=True), \
                patch.object(R,'verify_detector'):
            self.assertEqual(A.load_config().max_audio_seconds,1200)


if __name__=='__main__':unittest.main()
