"""Atomic workflow edits preserve original actions and isolate retrieval side effects."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workflow_research_embeddings import patch_notion, patch_news, ENDPOINT


class ResearchWorkflowTests(unittest.TestCase):
    def test_notion_archive_after_save_and_original_context_preserved(self):
        fixture = {'nodes': [{'name': 'AI Agent', 'parameters': {'text': 'Current context:\n{{ $json.context }}',
                    'options': {'systemMessage': 'Retain full replacement context'}}}]}
        operations = patch_notion(fixture, 'existing-profile-credential')
        changes = [op for op in operations if op['type'] == 'updateNodeParameters']
        self.assertTrue(changes[0]['parameters']['text'].startswith(fixture['nodes'][0]['parameters']['text']))
        self.assertEqual(changes[0]['parameters']['options'], fixture['nodes'][0]['parameters']['options'])
        self.assertIn({'type': 'addConnection', 'source': 'HTTP Request', 'target': 'Prepare saved research archive',
                       'connectionType': 'main', 'sourceIndex': 0, 'targetIndex': 0}, operations)
        self.assertFalse(any(op['type'] in {'removeNode', 'setWorkflowSettings'} for op in operations))

    def test_news_archive_after_validation_and_bounded_candidates(self):
        fixture = {'nodes': [{'name': 'Prepare five stories and comments', 'parameters': {
            'jsCode': 'if (comments.length >= 8) break; return { discussionUrl, comments };'}},
            {'name': 'Summarize headlines and discussion', 'parameters': {'messages': {'messageValues': [{'message': 'Original prompt'}]}}}]}
        operations = patch_news(fixture, 'existing-profile-credential')
        prepare = next(op for op in operations if op.get('nodeName') == 'Prepare five stories and comments')
        self.assertIn('comments.length >= 20', prepare['parameters']['jsCode'])
        self.assertIn('comments: comments.slice(0,8)', prepare['parameters']['jsCode'])
        self.assertTrue(any(op.get('source') == 'Format and validate Telegram digest' and op.get('target') == 'Prepare validated news archive' for op in operations))
        requests = [op['node'] for op in operations if op['type'] == 'addNode' and op['node']['type'].endswith('httpRequest')]
        self.assertEqual(len(requests), 3)
        for node in requests:
            self.assertEqual(node['parameters']['url'], ENDPOINT)
            self.assertEqual(node['parameters']['options']['timeout'], 18000)
            self.assertEqual(node['parameters']['options']['batching'], {'batch': {'batchSize': 2, 'batchInterval': 250}})
            self.assertFalse(node['parameters']['options']['redirect']['redirect']['followRedirects'])
            self.assertEqual(node['credentials']['httpHeaderAuth']['id'], 'existing-profile-credential')
        self.assertFalse(any(op['type'] == 'setWorkflowSettings' for op in operations))

    def test_existing_patch_refuses_duplicate_insertion(self):
        with self.assertRaises(ValueError):
            patch_notion({'nodes': [{'name': 'Prepare page research retrieval'}]}, 'credential')
        with self.assertRaises(ValueError):
            patch_news({'nodes': [{'name': 'Prepare news semantic requests'}]}, 'credential')


if __name__ == '__main__': unittest.main()
