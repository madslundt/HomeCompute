"""Audio API migration preserves unrelated settings and fails closed on custom providers."""
import importlib.util
from pathlib import Path
import unittest
SPEC=importlib.util.spec_from_file_location('webui_speech',Path(__file__).resolve().parents[1]/'scripts/setup-open-webui-speech.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)
class SpeechTests(unittest.TestCase):
 def test_migration_preserves_other_settings_and_is_idempotent(self):
  before={k:{'ENGINE':'','OPENAI_API_BASE_URL':'https://api.openai.com/v1','WHISPER_MODEL':'base','custom_preserved':{'value':3}} for k in ('stt','tts')}
  after=module.candidate(before,'synthetic-key')
  self.assertEqual(before['stt']['ENGINE'],'')
  self.assertEqual(after['stt']['MODEL'],'whisper')
  self.assertEqual(after['tts']['MODEL'],'tts-danish')
  self.assertEqual(after['tts']['OPENAI_PARAMS'],{'response_format':'wav'})
  self.assertEqual(after['stt']['custom_preserved'],before['stt']['custom_preserved'])
  self.assertEqual(module.candidate(after,'synthetic-key'),after)
 def test_foreign_providers_and_parameters_are_not_overwritten(self):
  for override in ({'ENGINE':'azure'},{'OPENAI_API_BASE_URL':'https://foreign.example/v1'},{'OPENAI_PARAMS':{'instructions':'foreign'}}):
   before={k:{'ENGINE':'','OPENAI_API_BASE_URL':module.BASE} for k in ('stt','tts')}
   before['tts'].update(override)
   with self.assertRaises(ValueError):module.candidate(before,'synthetic-key')
if __name__=='__main__':unittest.main()
