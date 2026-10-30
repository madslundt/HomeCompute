"""Checks prevent benchmark scoring from overstating partial or invalid answers."""
import importlib.util
from pathlib import Path
import unittest
import tempfile

spec = importlib.util.spec_from_file_location('comparison', Path(__file__).resolve().parents[1]/'scripts/assistant-comparison.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Scoring(unittest.TestCase):
    def test_missing_null_is_not_evidence(self):
        result = module.grade({'expected_json':{'price':None}}, '{}')
        self.assertFalse(result['semantic_pass'])

    def test_false_is_not_zero(self):
        result = module.grade({'expected_json':{'sent':False}}, '{"sent":0}')
        self.assertFalse(result['semantic_pass'])

    def test_fenced_correct_answer_is_semantic_only(self):
        result = module.grade({'expected_json':{'sent':False}}, '```json\n{"sent":false}\n```')
        self.assertTrue(result['semantic_pass'])
        self.assertFalse(result['strict_json'])
        self.assertFalse(result['pass'])

    def test_required_evidence_fields(self):
        case = {'expected_json':{'price':None},'required_items':{'missing':['dates','ages']}}
        self.assertFalse(module.grade(case,'{"price":null,"missing":["dates"]}')['pass'])
        self.assertTrue(module.grade(case,'{"price":null,"missing":["dates","ages"]}')['pass'])

    def test_relay_cannot_broaden_alias_or_enable_tools(self):
        for payload in [{'model':'another-model'}, {'model':'automation-moe','tools':[{}]}]:
            with self.assertRaises(ValueError):module.bounded_payload(payload)

    def test_both_token_parameter_forms_are_bounded(self):
        for key in ('max_tokens','max_completion_tokens'):
            original={'model':'automation-moe',key:8192}
            result=module.bounded_payload(original)
            self.assertEqual(result['max_tokens'],1024)
            self.assertNotIn('max_completion_tokens',result)
            self.assertEqual(original[key],8192)

    def test_shared_output_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);path.chmod(0o755)
            with self.assertRaises(ValueError):module.private_output(path)

    def test_unverified_and_yielded_turns_are_not_completion(self):
        for value in ({}, {'status':'ok'}, {'status':'ok','yielded':True}):
            self.assertFalse(module.native_completed(value,'synthetic-session'))

    def test_completed_identity_and_reroute_are_checked_independently(self):
        receipt={'sessionId':'s','effective':{'provider':'inference','model':'automation-moe','responseModel':'automation-moe'},'rerouted':False}
        value={'status':'ok','result':{'meta':{'aborted':False,'completion':{'finishReason':'stop','stopReason':'stop'},
               'agentMeta':{'sessionId':'s','provider':'inference','model':'automation-moe','terminalReceipt':receipt}}}}
        self.assertTrue(module.native_completed(value,'s'))
        self.assertFalse(module.native_completed(value,'another-session'))
        receipt['rerouted']=True
        self.assertFalse(module.native_completed(value,'s'))


if __name__ == '__main__':
    unittest.main()
