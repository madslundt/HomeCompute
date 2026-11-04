"""Context change preserves the complete model/authority projection and env wiring."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('task_context',ROOT/'scripts/configure-openclaw-task-context.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.before={'models':{'providers':{'inference':{'models':[{'id':'automation-moe','maxTokens':4096,'contextWindow':32768,'compat':{'supportsReasoningEffort':False}}], 'apiKey':'unused'}}},'channels':{'retained':True},'tools':{'deny':['exec']}}

    def test_only_context_is_normalized_and_input_is_unchanged(self):
        after=copy.deepcopy(self.before);after['models']['providers']['inference']['models'][0]['contextWindow']=65536
        self.assertEqual(m.projection(self.before),m.projection(after))
        self.assertEqual(self.before['models']['providers']['inference']['models'][0]['contextWindow'],32768)
        after['tools']['deny']=[];self.assertNotEqual(m.projection(self.before),m.projection(after))
        after=copy.deepcopy(self.before);after['models']['providers']['inference']['models'][0]['maxTokens']=8192
        with self.assertRaisesRegex(ValueError,'output contract'):m.projection(after)

    def test_environment_preserves_other_settings_and_rejects_duplicate_contexts(self):
        raw=b'NEMOCLAW_MODEL=automation-moe\nNEMOCLAW_CONTEXT_WINDOW=32768\nEXTERNAL_KEY_FILE=/external/ref\n'
        self.assertEqual(m.environment(raw,65536),raw.replace(b'32768',b'65536'))
        self.assertEqual(m.environment(m.environment(raw,65536),32768),raw)
        for bad in (b'NEMOCLAW_MODEL=automation-moe\n',raw+b'NEMOCLAW_CONTEXT_WINDOW=65536\n'):
            with self.assertRaisesRegex(ValueError,'Exact declared context'):m.environment(bad,65536)

    def test_stale_context_plan_never_mutates_native_or_host_files(self):
        result={'plan_sha256':'current'}
        with patch.object(m,'plan',return_value=(result,None,None,None,None,None)):
            with self.assertRaisesRegex(ValueError,'plan changed'):m.configure('a'*40,262144,'stale',True)


if __name__=='__main__':unittest.main()
