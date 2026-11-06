"""Language probes select acoustic geometry; hypothesis text never selects it."""
import importlib.util
import io
import itertools
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
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
        self.assertEqual([p['start_frame'] for p in audit['windows'][0]['partitions']],[0,64000])
        self.assertEqual([p['asr_language_windows'][0]['language_selection']['selected']
                          for p in audit['windows'][0]['partitions']],['da','en'])
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

    def test_long_combination_retains_actual_child_provenance_at_global_offsets(self):
        children=[(0,8,audio(8)),(8,8,audio(8))]
        replies=[result(duration=8),result(duration=8),result(' First window',duration=16),
                 result(language='da',duration=8),result(duration=8),
                 result(' Anden',language='da',duration=8),result(' Final',duration=8)]
        with patch.object(R._BOUNDARIES,'partitions',return_value=children), \
                patch.object(A,'_native_request',side_effect=replies):
            raw,_=A.transcribe_audio(audio(32),32,'verbose_json',[('language_candidates','da,en')],10,
                recover_trailing_speech=True,language_boundaries=True)
        value=json.loads(raw);audit=value['language_boundary_recovery']['windows'][0]
        self.assertEqual(audit['source_window_start_frame'],256000)
        actual=[child['asr_language_windows'][0] for child in audit['partitions']]
        self.assertEqual([(item['start'],item['end']) for item in actual],[(16,24),(24,32)])
        self.assertEqual([item['language_selection']['selected'] for item in actual],['da','en'])


if __name__=='__main__':unittest.main()
