#!/usr/bin/env python3
"""Actual adapter boundary and raw CPU-VAD bindings, using synthetic audio/native stubs."""
import importlib.util
from email.parser import BytesParser
from email import policy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import wave

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('local_adapter',ROOT/'deploy/whisper-cpp-stt/adapter.py')
A=importlib.util.module_from_spec(SPEC);sys.modules[SPEC.name]=A;SPEC.loader.exec_module(A)
R=A._RECOVERY_MODULE;V=R._VAD

def audio(seconds=3):
 out=io.BytesIO()
 with wave.open(out,'wb') as f:f.setparams((1,2,16000,0,'NONE','NONE'));f.writeframes(b'\x01\x00\xfe\xff'*int(seconds*8000))
 return out.getvalue()

def pcm(raw):
 with wave.open(io.BytesIO(raw),'rb') as f:return f.readframes(f.getnframes())

def response(text='Synthetic.',end=1):
 return json.dumps({'task':'transcribe','language':'english','duration':3,'text':text+'\n',
                    'segments':[{'start':0,'end':end,'text':text}]}).encode()

def wire(body, content_type):
 message=BytesParser(policy=policy.default).parsebytes(("Content-Type: "+content_type+"\r\nMIME-Version: 1.0\r\n\r\n").encode()+body)
 fields={};submitted=None
 for item in message.iter_parts():
  name=item.get_param("name",header="content-disposition");value=item.get_payload(decode=True)
  if name=="file":submitted=value
  else:fields[name]=value.decode()
 return fields,submitted

class AdapterTests(unittest.TestCase):
 def transcribe(self,source,**kw):
  return A.transcribe_audio(source,len(pcm(source))/32000,'verbose_json',[('response_format','verbose_json')],10,recover_trailing_speech=True,**kw)
 def test_default_off_is_byte_exact_and_never_computes_endpoint_vad(self):
  original=response();source=audio()
  with patch.object(A,'_native_request',return_value=original) as native,patch.object(R,'activity',return_value=[0.0]*94),patch.object(V,'speech_spans',side_effect=AssertionError('disabled')):
   actual=self.transcribe(source)
  self.assertEqual(actual,A.normalize_native_response(original,'verbose_json',3));self.assertEqual(native.call_count,1)
  fields,submitted=wire(*native.call_args.args[:2]);self.assertNotIn('homecompute_eot_spans',fields);self.assertEqual(submitted,source)
 def test_internal_spans_preserve_exact_audio_and_native_fields_in_one_call(self):
  source=audio();original=response()
  with patch.object(A,'_native_request',return_value=original) as native,patch.object(R,'candidate',return_value=None),patch.object(V,'speech_spans',return_value=[[0,8192],[10240,16000]]) as vad:
   actual=self.transcribe(source,local_eot_ranking=True)
  vad.assert_called_once_with(pcm(source));self.assertEqual(native.call_count,1)
  fields,submitted=wire(*native.call_args.args[:2]);self.assertEqual(fields['homecompute_eot_spans'],'0:8192;10240:16000');self.assertEqual(submitted,source)
  self.assertEqual(actual,A.normalize_native_response(original,'verbose_json',3))
 def test_nested_suffix_gets_spans_for_its_own_exact_raw_pcm(self):
  source=audio();seen=[]
  def spans(value):seen.append(value);return [[0,512]]
  with patch.object(A,'_native_request',side_effect=[response(end=1),response('Continuation.',end=2)]) as native,patch.object(R,'activity',side_effect=lambda value:[1.0]*((len(value)//2+511)//512)),patch.object(V,'speech_spans',side_effect=spans):
   self.transcribe(source,local_eot_ranking=True)
  self.assertEqual(native.call_count,2);self.assertEqual(seen,[pcm(source),pcm(source)[32000:]])
  for call,expected in zip(native.call_args_list,seen):
   fields,wav=wire(*call.args[:2]);self.assertEqual(fields['homecompute_eot_spans'],'0:512');self.assertEqual(pcm(wav),expected)
 def test_vad_work_uses_same_deadline_and_expiry_prevents_native_call(self):
  for elapsed in [4,11]:
   now=[0]
   def observe(_):now[0]=elapsed;return []
   with patch.object(A,'_native_request',return_value=response()) as native,patch.object(R,'candidate',return_value=None),patch.object(V,'speech_spans',side_effect=observe):
    if elapsed>10:
     with self.assertRaises(A.RequestError):R.transcribe(audio(),3,[('response_format','verbose_json')],10,request=A._native_request,codec=A._CODEC_MODULE,clock=lambda:now[0],local_eot_ranking=True)
     native.assert_not_called()
    else:
     R.transcribe(audio(),3,[('response_format','verbose_json')],10,request=A._native_request,codec=A._CODEC_MODULE,clock=lambda:now[0],local_eot_ranking=True)
     self.assertEqual(native.call_args.args[2],6)
 def test_unknown_vad_is_an_error_before_any_model_request(self):
  with patch.object(A,'_native_request') as native,patch.object(V,'speech_spans',side_effect=RuntimeError('synthetic')):
   with self.assertRaises(A.RequestError):self.transcribe(audio(),local_eot_ranking=True)
   native.assert_not_called()
 def test_user_cannot_supply_internal_span_metadata(self):
  with self.assertRaises(A.RequestError):
   A.parse_multipart(*A.make_native_multipart([('model','whisper'),('homecompute_eot_spans','0:512')],audio()))
 def test_language_probes_never_receive_endpoint_spans(self):
  source=audio(6);chunks=[(0,3,audio()),(3,3,audio())];calls=[]
  def native(body,kind,timeout):
   fields,wav=wire(body,kind);calls.append((fields,wav))
   if fields.get('detect_language')=='true':
    lang='da' if len(calls)==1 else 'en'
    return json.dumps({'task':'transcribe','duration':3,'text':'','segments':[],'language_selection':{'mode':'candidate_auto','candidates':['da','en'],'selected':lang,'detected':lang,'selected_probability':1.0,'detected_probability':1.0}}).encode()
   return response(end=6)
  with patch.object(R._BOUNDARIES,'partitions',return_value=chunks),patch.object(R._BOUNDARIES,'language_regions',return_value=[(0,6,source)]),patch.object(V,'speech_spans',return_value=[]) as vad,patch.object(R,'candidate',return_value=None):
   R.transcribe(source,6,[('response_format','verbose_json'),('language_candidates','da,en')],10,request=native,codec=A._CODEC_MODULE,language_boundaries=True,local_eot_ranking=True)
  self.assertEqual(len(calls),3);self.assertTrue(all('homecompute_eot_spans' not in fields for fields,_ in calls[:2]));self.assertEqual(calls[-1][0]['homecompute_eot_spans'],'');vad.assert_called_once_with(pcm(source))
 def test_feature_is_operator_opt_in_and_requires_existing_cuda_recovery(self):
  base={'WHISPER_CPP_HOME_COMPUTE_REVISION':'a'*40,'WHISPER_CPP_IMAGE_ID':'sha256:'+'b'*64}
  with patch.dict(os.environ,base,clear=True):self.assertFalse(A.load_config().local_eot_ranking)
  for extra in [{'WHISPER_CPP_LOCAL_EOT_RANKING':'true'},{'WHISPER_CPP_LOCAL_EOT_RANKING':'1'}]:
   with patch.dict(os.environ,base|extra,clear=True),self.assertRaises(ValueError):A.load_config()
  with patch.dict(os.environ,base|{'WHISPER_CPP_DEVICE':'cuda','WHISPER_CPP_RECOVER_TRAILING_SPEECH':'true','WHISPER_CPP_LOCAL_EOT_RANKING':'true'},clear=True),patch.object(R,'verify_detector'),patch.object(V,'speech_spans',return_value=[]):self.assertTrue(A.load_config().local_eot_ranking)

class FakeLibrary:
 def __init__(self,*,probability=.8,defaults_changed=False,native_error=False,bad_grid=False):
  self.arguments=None;self.segment_params=None;self.context_params=None;self.log=None
  self.whisper_log_set=Mock(side_effect=lambda log,_:setattr(self,'log',log))
  self.whisper_vad_default_context_params=Mock(side_effect=lambda:V.Params(4,True,3))
  def initialize(path,params):self.context_params=(params.n_threads,params.use_gpu,params.gpu_device);return 1
  self.whisper_vad_init_from_file_with_params=Mock(side_effect=initialize)
  def detect(ctx,pointer,count):
   self.arguments=[pointer[i] for i in range(count)];self.count=(count+511)//512
   if native_error:self.log(4,None,None)
   return True
  self.whisper_vad_detect_speech=Mock(side_effect=detect)
  self.whisper_vad_n_probs=Mock(side_effect=lambda _:self.count)
  self.whisper_vad_probs=Mock(side_effect=lambda _:[probability]*self.count)
  self.whisper_vad_default_params=Mock(side_effect=lambda:V.SegmentParams(.5,251 if defaults_changed else 250,100,float.fromhex('0x1.fffffep+127'),30,.1))
  def segments(ctx,params):self.segment_params=(params.threshold,params.min_speech_duration_ms,params.min_silence_duration_ms,params.speech_pad_ms);return 2
  self.whisper_vad_segments_from_probs=Mock(side_effect=segments)
  self.whisper_vad_segments_n_segments=Mock(return_value=1)
  self.whisper_vad_segments_get_segment_t0=Mock(return_value=1 if bad_grid else 0)
  self.whisper_vad_segments_get_segment_t1=Mock(return_value=51)
  self.whisper_vad_free_segments=Mock();self.whisper_vad_free=Mock()

class RawVADBindingTests(unittest.TestCase):
 def test_cpu_one_thread_default_native_segments_and_no_padding_or_gain(self):
  lib=FakeLibrary();value=b'\x01\x00\xfe\xff'*4000
  with patch.object(V,'verify_model'),patch.object(V.C,'CDLL',return_value=lib):spans=V.speech_spans(value)
  self.assertEqual(spans,[[0,8000]]);self.assertEqual(lib.context_params,(1,False,0));self.assertEqual(lib.segment_params,(.5,250,100,0))
  self.assertEqual(lib.arguments[:4],[1/32768,-2/32768,1/32768,-2/32768]);self.assertEqual(lib.whisper_vad_detect_speech.call_count,1)
  lib.whisper_vad_free_segments.assert_called_once_with(2);lib.whisper_vad_free.assert_called_once_with(1)
 def test_all_probabilities_and_defaults_are_validated_and_context_freed(self):
  for kw in [{'probability':float('nan')},{'probability':1.01},{'defaults_changed':True},{'native_error':True},{'bad_grid':True}]:
   lib=FakeLibrary(**kw)
   with self.subTest(kw=kw),patch.object(V,'verify_model'),patch.object(V.C,'CDLL',return_value=lib),self.assertRaises(RuntimeError):V.speech_spans(b'\x01\x00'*8000)
   lib.whisper_vad_free.assert_called_once_with(1)
 def test_unbounded_or_malformed_vad_input_never_loads_native_library(self):
  for value in [b'',b'x',b'\0'*(30*32000+2),bytearray(1024)]:
   with self.subTest(length=len(value)),patch.object(V.C,'CDLL') as lib,self.assertRaises(ValueError):V.speech_spans(value)
   lib.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
