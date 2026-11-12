"""OpenBot desired roster keeps local effort and cloud boundaries explicit."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OpenBotAgentRosterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster = json.loads((ROOT / 'config/openbot-agent-roster.json').read_text())
        cls.agents = {agent['id']: agent for agent in cls.roster['agents']}

    def test_every_local_assignment_requires_xhigh(self):
        self.assertEqual(self.roster['default_reasoning_effort'], 'xhigh')
        self.assertEqual(self.roster['default_provider'], 'Hermes')
        assignments = []
        for agent in self.roster['agents']:
            assignments.append((agent['id'], agent['model']))
            assignments.extend((agent['id'], model) for model in agent.get('backup_models', []))
        assignments.append(('hermes-provider', {
            'provider': 'Hermes',
            'model': self.roster['hermes_provider']['model'],
            'reasoning_effort': self.roster['hermes_provider']['reasoning_effort'],
        }))
        for owner, assignment in assignments:
            if assignment.get('model') == 'automation-moe':
                with self.subTest(owner=owner):
                    self.assertEqual(assignment.get('provider'), 'Hermes')
                    self.assertEqual(assignment.get('reasoning_effort'), 'xhigh')

    def test_engineer_rename_preserves_existing_openbot_identity(self):
        engineer = self.agents['engineer']
        self.assertEqual(engineer['name'], 'Engineer')
        self.assertEqual(engineer['server_id'], 'agent-844df777-3ee6-4dcd-a12d-28711b575b2e')

    def test_lead_engineer_is_cloud_only_and_matches_live_ui_identity(self):
        lead = self.agents['lead-engineer']
        self.assertEqual(lead['server_id'], 'agent-efe56fb2-ca0e-44e3-9acd-db6813c91d5a')
        self.assertEqual(lead['lifecycle'], 'active')
        self.assertEqual(lead['model'], {
            'provider': 'ChatGPT', 'model': 'GPT-6-Luna', 'reasoning_effort': 'high'
        })
        self.assertEqual(lead['permissions'], {
            'access': 'workspace-only', 'computer_use': False,
            'local_scripts': False, 'automatic_routines': False
        })

    def test_chatgpt_subscription_provider_is_scoped_to_three_litellm_aliases(self):
        provider = self.roster['chatgpt_subscription_provider']
        self.assertEqual(provider['provider_id'], 'homecompute-chatgpt')
        self.assertEqual(provider['base_url'], 'https://mcp.home.arpa/v1')
        self.assertEqual(
            [model['id'] for model in provider['models']],
            ['chatgpt-sol-latest', 'chatgpt-terra-latest', 'chatgpt-luna-latest'],
        )
        self.assertEqual(provider['api_key_scope'], 'these three ChatGPT aliases only; MCP access denied')

    def test_researcher_uses_litellm_sol_and_keeps_its_local_backup(self):
        researcher = self.agents['researcher']
        self.assertEqual(researcher['model'], {
            'provider': 'HomeCompute ChatGPT subscription',
            'model': 'chatgpt-sol-latest',
            'reasoning_effort': 'high',
        })
        self.assertEqual(researcher['backup_models'][0]['provider'], 'Hermes')
        self.assertEqual(researcher['backup_models'][0]['model'], 'automation-moe')


if __name__ == '__main__':
    unittest.main()
