from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workflow_battery_embeddings import patch


class BatteryWorkflowTests(unittest.TestCase):
    def test_only_inserts_retrieval_and_archive_no_existing_threshold_or_validator_edits(self):
        for weekly in (False, True):
            source, model, validated = (('Prepare Review Snapshot', 'Generate Battery Review (Local MoE)', 'Format Telegram Report')
                if weekly else ('Select LLM Review', 'Analyze Plan and Battery Context', 'Validate LLM Review'))
            fixture = {'nodes': [{'name': name, 'parameters': {'jsCode': 'sensitive numeric thresholds'}} for name in (source, model, validated)],
                       'nodeGroups': [{'name': 'Review', 'nodeNames': [source, model, validated]}]}
            before = deepcopy(fixture)
            operations = patch(fixture, 'existing-credential', weekly=weekly)
            self.assertEqual(fixture, before)
            self.assertFalse(any(op['type'] in {'updateNodeParameters', 'setNodeParameter', 'setWorkflowSettings', 'removeNode'} for op in operations))
            self.assertEqual(sum(op['type'] == 'addNode' for op in operations), 5)
            self.assertTrue(any(op['type'] == 'addConnection' and op.get('source') == validated for op in operations))
            self.assertEqual(sum(op['type'] == 'removeConnection' for op in operations), 1)
            self.assertEqual(sum(op['type'] == 'updateNodeGroup' for op in operations), 1)
            for operation in operations:
                if operation['type'] == 'addNode' and operation['node']['type'].endswith('httpRequest'):
                    self.assertEqual(operation['node']['parameters']['options']['batching']['batch']['batchSize'], 2)


if __name__ == '__main__': unittest.main()
