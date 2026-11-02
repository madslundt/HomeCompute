"""HA migration changes only the selected primary speech reference."""
import importlib.util
from pathlib import Path
import unittest
SPEC=importlib.util.spec_from_file_location('ha_speech',Path(__file__).resolve().parents[1]/'scripts/setup-ha-speech.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)
class MigrationTests(unittest.TestCase):
 def test_preserves_all_other_pipeline_fields_and_is_idempotent(self):
  before={'id':'synthetic','name':'Home AI','stt_engine':module.OLD,'stt_language':'da','tts_engine':module.TTS,'tts_voice':'danish-default','conversation_engine':'preserved','prefer_local_intents':False}
  after=module.pipeline_candidate(before)
  self.assertEqual(after,{**before,'stt_engine':module.NEW})
  self.assertEqual(before['stt_engine'],module.OLD)
  self.assertEqual(module.pipeline_candidate(after),after)
 def test_unexpected_selection_is_preserved(self):
  for change in ({'stt_engine':'stt.foreign'},{'stt_language':'en'},{'tts_engine':'tts.foreign'}):
   before={'stt_engine':module.OLD,'stt_language':'da','tts_engine':module.TTS,'tts_voice':'danish-default',**change}
   with self.assertRaises(ValueError):module.pipeline_candidate(before)
if __name__=='__main__':unittest.main()
