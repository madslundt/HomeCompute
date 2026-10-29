"""User-selected names stay consistent across native agents and chat accounts."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentRosterTests(unittest.TestCase):
    def test_space_names_use_stable_role_and_account_identifiers_everywhere(self):
        team = json.loads((ROOT / 'config/openclaw-team.json').read_text())['agents']
        native = load('native_team', 'setup-openclaw-team.py')
        chat = load('native_chat', 'setup-openclaw-mattermost.py')
        bootstrap = load('bootstrap_roster', 'bootstrap-mattermost.py')
        names = {'main': 'Atlas', 'personal': 'Luna', 'researcher': 'Nova', 'engineer': 'Apollo',
                 'operator': 'Orion', 'shopper': 'Vega', 'keeper': 'Ceres', 'travel': 'Lyra',
                 'cook': 'Hestia', 'administrator': 'Themis', 'finance': 'Plutus', 'family': 'Gaia'}
        self.assertEqual(set(team), set(names))
        self.assertEqual(set(native.ROSTER), set(names))
        self.assertEqual(len(bootstrap.AGENT_ACCOUNTS), len(names))
        self.assertEqual(set(chat.ACCOUNTS), set(bootstrap.AGENT_ACCOUNTS))
        for account, (role, username, _) in bootstrap.AGENT_ACCOUNTS.items():
            self.assertEqual(team[role]['name'], names[role])
            self.assertEqual(username, names[role].lower())
            self.assertEqual(chat.ACCOUNTS[account], (role, username))
            self.assertEqual(account, 'default' if role == 'main' else role)

    def test_topic_specialists_match_their_finite_capabilities(self):
        native = load('native_roles', 'setup-openclaw-team.py')
        topics = load('topic_roster', 'setup-mattermost-n8n.py')
        expected = {'shopping': 'shopper', 'travel': 'travel',
                    'general-shopping': 'researcher', 'finances': 'finance', 'family': 'personal'}
        for channel, role in expected.items():
            members = {topics.TOPICS[channel][1]}
            extra = getattr(topics, 'ADDITIONAL_SPECIALISTS', {}).get(channel)
            if extra:
                members.add(extra)
            self.assertIn(role, members)
        bootstrap = load('merged_cook_roster', 'bootstrap-mattermost.py')
        self.assertEqual(bootstrap.AGENT_ACCOUNTS['cook'][2], 'shopping')
        self.assertEqual(topics.ALIASES['meals'], 'shopping')
        services = native.ROLE_SERVICE_TOOLS
        prefix = native.MCP_PREFIX
        self.assertTrue({prefix + name for name in ('shopping_list', 'discount_watch', 'shopping_search')} <= services['shopper'])
        self.assertTrue({prefix + name for name in ('storage_list', 'storage_adjust')} <= services['keeper'])
        self.assertFalse(services['shopper'] & {prefix + name for name in ('school_posts', 'storage_list', 'web_search')})
        self.assertFalse(services['keeper'] & {prefix + name for name in ('school_posts', 'shopping_add', 'web_search')})
        self.assertEqual(services['travel'], {prefix + name for name in
                         ('web_search', 'research_search', 'calendar_list', 'calendar_events', 'calendar_availability')})


if __name__ == '__main__':
    unittest.main()
