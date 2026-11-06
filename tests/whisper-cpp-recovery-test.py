"""Bounded suffix recovery at the actual adapter/native-request seam."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('recovery_adapter',ROOT/'deploy/whisper-cpp-stt/adapter.py')
A=importlib.util.module_from_spec(spec);sys.modules[spec.name]=A;spec.loader.exec_module(A)


def audio(seconds=3):
    out=io.BytesIO()
    with wave.open(out,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes((b'\x01\x00\x02\x00'*int(seconds*8000)))
    return out.getvalue()


def raw(text=' Head',start=0,end=1,language='english'):
    return json.dumps({'text':text+'\n','segments':[{'text':text,'start':start,'end':end}],
                       'language':language,'task':'transcribe','duration':3}).encode()


def speech(pcm):
    return [1.0] * ((len(pcm)//2+511)//512)


class RecoveryTests(unittest.TestCase):
    def test_suffix_keeps_all_right_context_and_text_once(self):
        source=audio()
        with patch.object(A,'_native_request',side_effect=[raw(),raw(' Tail',end=2)]) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=speech):
            body,_=A.transcribe_audio(source,3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True)
        self.assertEqual(request.call_count,2)
        _fields,suffix=A.parse_multipart(request.call_args_list[1].args[0],request.call_args_list[1].args[1])
        with wave.open(io.BytesIO(source),'rb') as f:pcm=f.readframes(f.getnframes())
        with wave.open(io.BytesIO(suffix),'rb') as f:tail=f.readframes(f.getnframes())
        self.assertEqual(tail,pcm[32000:])
        value=json.loads(body);self.assertEqual(value['text'],' Head\n Tail\n')
        self.assertEqual([(s['start'],s['end']) for s in value['segments']],[(0,1),(1,3)])
        self.assertEqual([(s['start_offset'],s['end_offset']) for s in value['segments']],[(0,5),(6,11)])
        self.assertFalse(value['suffix_recovery']['coverage_claim'])

    def test_quiet_or_short_vad_false_positives_leave_response_byte_identical(self):
        def short(pcm):
            result = [0.0] * ((len(pcm)//2+511)//512)
            result[32:41] = [1.0]*9
            return result
        original=raw();expected=A.normalize_native_response(original,'verbose_json',3)
        with patch.object(A,'_native_request',return_value=original) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=short):
            self.assertEqual(A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True),expected)
        self.assertEqual(request.call_count,1)

    def test_missing_or_invalid_timing_never_supplies_an_invented_seek(self):
        for payload in ({'text':' Head\n','segments':[]},json.loads(raw(end=4)),json.loads(raw(end=0))):
            encoded=json.dumps(payload).encode()
            with self.subTest(payload=payload),patch.object(A,'_native_request',return_value=encoded) as request, \
                    patch.object(A._RECOVERY_MODULE,'activity',side_effect=speech):
                A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True)
                self.assertEqual(request.call_count,1)

    def test_vad_failure_cannot_silently_publish_a_different_partial_transcript(self):
        with patch.object(A,'_native_request',return_value=raw()) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=RuntimeError('synthetic')), \
                self.assertRaises(A.RequestError):
            A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True)
        self.assertEqual(request.call_count,1)

    def test_no_recursive_recovery_and_no_suffix_failure_hidden_as_success(self):
        with patch.object(A,'_native_request',side_effect=[raw(),raw(' Still short',end=.1)]) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=speech):
            A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True)
            self.assertEqual(request.call_count,2)
        with patch.object(A,'_native_request',side_effect=[raw(),A.RequestError(502,'fixed')]), \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=speech),self.assertRaises(A.RequestError):
            A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True)

    def test_disabled_policy_never_observes_vad_or_changes_the_native_boundary(self):
        with patch.object(A,'_native_request',return_value=raw()) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=AssertionError('disabled')):
            A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10)
        self.assertEqual(request.call_count,1)

    def test_timestamp_grace_avoids_repeating_already_transcribed_final_words(self):
        def drifting(pcm):
            values=[0.0]*((len(pcm)//2+511)//512)
            values[32:56]=[1.0]*24  # Speech ends0.8s after native's reported end.
            return values
        expected=A.normalize_native_response(raw(),'verbose_json',3)
        with patch.object(A,'_native_request',return_value=raw()) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=drifting):
            actual=A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True)
        self.assertEqual(actual,expected);self.assertEqual(request.call_count,1)

    def test_later_short_speech_is_still_supported_after_the_grace_interval(self):
        def later(pcm):
            values=[0.0]*((len(pcm)//2+511)//512);values[63:84]=[1.0]*21
            return values
        with patch.object(A,'_native_request',side_effect=[raw(),raw(' Later',end=2)]) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=later):
            A.transcribe_audio(audio(),3,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True)
        self.assertEqual(request.call_count,2)

    def test_one_deadline_decreases_across_both_native_calls(self):
        ticks=iter([0,0,1,2,3,4])
        with patch.object(A,'_native_request',side_effect=[raw(),raw(' Tail',end=2)]) as request, \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=speech):
            A._RECOVERY_MODULE.transcribe(audio(),3,[('response_format','verbose_json')],10,
                request=A._native_request,codec=A._CODEC_MODULE,clock=lambda:next(ticks))
        self.assertEqual([c.args[2] for c in request.call_args_list],[10,8])

    def test_long_input_conserves_all_pcm_and_preserves_language_fields(self):
        original=audio(32);parts=[]
        def native(body,content_type,timeout):
            fields,data=A.parse_multipart(body,content_type);parts.append(data)
            self.assertEqual(fields,{'response_format':'verbose_json','language_candidates':'da,en','language':'auto'})
            with wave.open(io.BytesIO(data),'rb') as f:seconds=f.getnframes()/16000
            return raw(' Complete',end=seconds)
        with patch.object(A,'_native_request',side_effect=native), \
                patch.object(A._RECOVERY_MODULE,'activity',side_effect=AssertionError('complete')):
            A.transcribe_audio(original,32,'verbose_json',[('response_format','verbose_json'),('language_candidates','da,en'),('language','auto')],10,recover_trailing_speech=True)
        def pcm(data):
            with wave.open(io.BytesIO(data),'rb') as f:return f.readframes(f.getnframes())
        self.assertEqual(len(parts),2);self.assertEqual(b''.join(pcm(p) for p in parts),pcm(original))

    def test_formats_without_native_timing_keep_the_existing_request(self):
        for format_,response in [('json',b'{"text":"Original"}'),('text',b'Original')]:
            with patch.object(A,'_native_request',return_value=response) as request, \
                    patch.object(A._RECOVERY_MODULE,'activity',side_effect=AssertionError('no timestamps')):
                A.transcribe_audio(audio(),3,format_,[('response_format',format_)],10,recover_trailing_speech=True)
            self.assertEqual(request.call_count,1)

    def test_opt_in_requires_a_verified_detector_and_cuda_asr(self):
        env={'WHISPER_CPP_HOME_COMPUTE_REVISION':'1'*40,'WHISPER_CPP_IMAGE_ID':'sha256:'+'2'*64,
             'WHISPER_CPP_DEVICE':'cuda','WHISPER_CPP_RECOVER_TRAILING_SPEECH':'true'}
        with patch.dict(os.environ,env,clear=True),patch.object(A._RECOVERY_MODULE,'verify_detector') as verify:
            self.assertTrue(A.load_config().recover_trailing_speech);verify.assert_called_once()
        with patch.dict(os.environ,{**env,'WHISPER_CPP_DEVICE':'cpu'},clear=True),self.assertRaises(ValueError):A.load_config()


class NativeVadTests(unittest.TestCase):
    def fake(self):
        v=A._RECOVERY_MODULE._VAD;values=(v.C.c_float*1)(.9)
        lib=SimpleNamespace(**{k:Mock() for k in ('whisper_vad_default_context_params','whisper_vad_init_from_file_with_params','whisper_vad_detect_speech','whisper_vad_n_probs','whisper_vad_probs','whisper_vad_free','whisper_log_set')})
        lib.whisper_vad_default_context_params.return_value=v.Params(4,True,0)
        lib.whisper_vad_init_from_file_with_params.return_value=1
        lib.whisper_vad_detect_speech.return_value=True
        lib.whisper_vad_n_probs.return_value=1;lib.whisper_vad_probs.return_value=values
        return v,lib

    def test_native_vad_is_cpu_only_normalizes_pcm_and_always_frees_context(self):
        v,lib=self.fake()
        with patch.object(v,'verify_model'),patch.object(v.C,'CDLL',return_value=lib):
            result=v.probabilities(b'\x00\x80\xff\x7f')
        params=lib.whisper_vad_init_from_file_with_params.call_args.args[1]
        self.assertFalse(params.use_gpu);self.assertEqual(params.n_threads,1)
        pointer=lib.whisper_vad_detect_speech.call_args.args[1]
        self.assertEqual(pointer[0],-1);self.assertEqual(pointer[1],32767/32768)
        self.assertAlmostEqual(result[0],.9);lib.whisper_vad_free.assert_called_once_with(1)

    def test_native_failure_or_inconsistent_frame_count_never_returns_partial_probabilities(self):
        for failed in ('detect','count'):
            v,lib=self.fake()
            if failed=='detect':lib.whisper_vad_detect_speech.return_value=False
            else:lib.whisper_vad_n_probs.return_value=2
            with patch.object(v,'verify_model'),patch.object(v.C,'CDLL',return_value=lib),self.assertRaises(RuntimeError):
                v.probabilities(b'\0\0'*2)
            lib.whisper_vad_free.assert_called_once_with(1)

    def test_unverified_model_never_loads_a_native_library(self):
        v,_=self.fake()
        with patch.object(v,'verify_model',side_effect=RuntimeError('synthetic')),patch.object(v.C,'CDLL') as load,self.assertRaises(RuntimeError):
            v.probabilities(b'\0\0'*2)
        load.assert_not_called()


if __name__=='__main__':unittest.main()
