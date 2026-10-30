"""Narrow reasoning metadata changes must preserve authority and deeper thinking."""
import copy
import importlib.util
import json
import sys
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('performance',Path(__file__).resolve().parents[1]/'scripts/openclaw_performance.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class Performance(unittest.TestCase):
    def fixture(self):
        return {'models':{'providers':{'inference':{'baseUrl':'https://inference.local/v1','api':'openai-completions',
                'apiKey':'unused','models':[{'id':'automation-moe','reasoning':False,'contextWindow':32768,
                'maxTokens':4096,'thinkingLevelMap':{'high':'high'},'compat':{'supportsStore':False}}]}}},
                'agents':{'defaults':{'thinkingDefault':'off','model':{'fallbacks':[]}}},
                'tools':{'deny':['exec']},'gateway':{'reload':{'mode':'off'}}}

    def test_only_qualified_model_reasoning_metadata_changes(self):
        before=self.fixture();snapshot=copy.deepcopy(before);after=module.reasoning_candidate(before)
        self.assertEqual(before,snapshot)
        for key in ('agents','tools','gateway'):self.assertEqual(before[key],after[key])
        model=after['models']['providers']['inference']['models'][0]
        self.assertEqual(model['thinkingLevelMap'],{'high':'high','off':'none'})
        self.assertTrue(model['compat']['supportsReasoningEffort'])
        self.assertIn('none',model['compat']['supportedReasoningEfforts'])
        self.assertFalse(model['compat']['supportsStore'])
        self.assertEqual(model['maxTokens'],4096)

    def test_idempotent_metadata_candidate(self):
        after=module.reasoning_candidate(self.fixture())
        self.assertEqual(module.reasoning_candidate(after),after)

    def test_wrong_endpoint_and_duplicate_alias_refused(self):
        for duplicate in (False,True):
            before=self.fixture();provider=before['models']['providers']['inference']
            if duplicate:provider['models']*=2
            else:provider['baseUrl']='https://unreviewed.invalid/v1'
            with self.assertRaises(ValueError):module.reasoning_candidate(before)

    def test_unknown_runtime_cannot_be_patched(self):
        with self.assertRaises(ValueError):module.browser_candidate(b'unknown runtime','file:///operator/helper.mjs')

    def test_unqualified_reasoning_candidate_is_not_promoted(self):
        path=Path(__file__).resolve().parents[1]/'config/openclaw-nemoclaw.json'
        overlay=json.loads(path.read_text())
        model=overlay['models']['providers']['inference']['models'][0]
        self.assertFalse(model['compat']['supportsReasoningEffort'])
        self.assertEqual(overlay['agents']['defaults']['thinkingDefault'],'off')

    def test_canary_preserves_loop_guard_and_excludes_production_capabilities(self):
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        sys.path.insert(0,str(scripts))
        try:
            spec=importlib.util.spec_from_file_location('qualifier',scripts/'qualify-openclaw-performance.py')
            qualifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(qualifier)
        finally:sys.path.pop(0)
        live=self.fixture()
        live['tools']['loopDetection']={'enabled':True,'historySize':20}
        live['browser']={'enabled':True}
        live['plugins']={'load':{'paths':['/trusted/safety-plugin','/private/other-plugin']}}
        after=qualifier.configuration(live,Path('/synthetic/workspace'))
        self.assertEqual(after['tools']['loopDetection'],live['tools']['loopDetection'])
        self.assertEqual(after['agents']['defaults']['timeoutSeconds'],30)
        self.assertNotIn('memory',after)
        self.assertNotIn('channels',after)
        self.assertEqual(after['tools']['allow'],['read','browser','session_status'])
        self.assertIn('write',after['tools']['deny'])
        self.assertEqual(after['plugins']['allow'],['homecompute-safety'])
        for path in ['/sandbox/.openclaw/perf-test','/tmp/perf-test','relative/perf-test']:
            with self.assertRaises(ValueError):qualifier.validate_output(Path(path))


if __name__=='__main__':unittest.main()
