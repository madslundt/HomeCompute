"""Noise decisions at the real adapter/native response boundary."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import wave

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('non_speech_adapter',ROOT/'deploy/whisper-cpp-stt/adapter.py')
A=importlib.util.module_from_spec(spec);sys.modules[spec.name]=A;spec.loader.exec_module(A)
R=A._RECOVERY_MODULE


def audio(seconds=4,value=1):
    out=io.BytesIO()
    with wave.open(out,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes(value.to_bytes(2,'little',signed=True)*round(seconds*16000))
    return out.getvalue()


def native(text=' Arbitrary output',seconds=4,probability=.9):
    return json.dumps({'text':text+'\n','segments':[{'text':text,'start':0,'end':seconds,
                       'no_speech_prob':probability}],'language':'english','task':'transcribe',
                       'duration':seconds}).encode()


def quiet(data):
    return [.1]*((len(data)//2+511)//512)


class NonSpeechWindowTests(unittest.TestCase):
    def transcribe(self,data,response,observation=quiet,enabled=True):
        with patch.object(A,'_native_request',return_value=response) as request, \
                patch.object(R,'activity',side_effect=observation):
            result=A.transcribe_audio(data,4,'verbose_json',[('response_format','verbose_json')],10,
                recover_trailing_speech=True,filter_non_speech=enabled)
        self.assertEqual(request.call_count,1)
        return result

    def test_exact_zero_returns_empty_without_invented_language_or_timing(self):
        body,_=self.transcribe(audio(value=0),native(probability=.01),observation=lambda _:self.fail('zero needs no detector'))
        value=json.loads(body);self.assertEqual(value['text'],'');self.assertEqual(value['segments'],[])
        self.assertNotIn('language',value)
        self.assertEqual(value['non_speech_filter']['windows'][0]['method'],'exact_zero_pcm')

    def test_both_negative_models_allow_empty_regardless_of_hypothesis_words(self):
        body,_=self.transcribe(audio(),native(text=' Different arbitrary output'))
        value=json.loads(body);self.assertEqual(value['text'],'')
        self.assertEqual(value['non_speech_filter']['windows'][0]['method'],'normalized_vad_and_native_no_speech')
        self.assertEqual(value['non_speech_filter']['native_calls'],1)

    def test_either_positive_signal_preserves_the_complete_canonical_response(self):
        for probability,observation in [(.1,lambda _:self.fail('speech confidence preserves audio')),
                                        (.9,lambda data:[.9]*((len(data)//2+511)//512))]:
            raw=native(probability=probability)
            with self.subTest(probability=probability):
                self.assertEqual(self.transcribe(audio(),raw,observation),A.normalize_native_response(raw,'verbose_json',4))

    def test_disabled_filter_keeps_the_qualified_response_unchanged(self):
        raw=native()
        self.assertEqual(self.transcribe(audio(value=0),raw,enabled=False),
                         A.normalize_native_response(raw,'verbose_json',4))

    def test_failed_observation_is_an_error_instead_of_partial_success(self):
        with self.assertRaises(A.RequestError):
            self.transcribe(audio(),native(),observation=lambda _:(_ for _ in ()).throw(RuntimeError('synthetic')))

    def test_empty_first_window_keeps_later_speech_anchors_and_source_offset(self):
        source=audio(16,0);speech=audio(16,1)
        def windows(_):
            return iter([(0,16,source),(16,16,speech)])
        replies=[native(seconds=16),native(' Retained speech',seconds=16,probability=.1)]
        with patch.object(A._CODEC_MODULE,'native_audio_windows',side_effect=windows), \
                patch.object(A,'_native_request',side_effect=replies),patch.object(R,'activity',side_effect=quiet):
            raw,_=A.transcribe_audio(audio(32),32,'verbose_json',[],10,
                recover_trailing_speech=True,filter_non_speech=True)
        value=json.loads(raw);self.assertEqual(value['text'],' Retained speech\n')
        self.assertEqual([(s['start'],s['end'],s['start_offset'],s['end_offset']) for s in value['segments']],[(16,32,0,16)])
        self.assertIsNone(value['language_windows'][0]['language'])
        self.assertEqual(value['language'],'english')

    def test_filter_configuration_requires_the_verified_cuda_recovery_runtime(self):
        env={'WHISPER_CPP_HOME_COMPUTE_REVISION':'1'*40,'WHISPER_CPP_IMAGE_ID':'sha256:'+'2'*64,
             'WHISPER_CPP_FILTER_NON_SPEECH':'true','WHISPER_CPP_DEVICE':'cuda'}
        with patch.dict(os.environ,env,clear=True),self.assertRaises(ValueError):A.load_config()
        with patch.dict(os.environ,{**env,'WHISPER_CPP_RECOVER_TRAILING_SPEECH':'true'},clear=True), \
                patch.object(R,'verify_detector'):
            self.assertTrue(A.load_config().filter_non_speech)


if __name__=='__main__':unittest.main()
