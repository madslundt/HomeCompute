"""Read-only live OMH admission and local-policy proof; no private payload output."""
import json
from pathlib import Path
import sys

sys.path.insert(0, '/opt/hermes')
import yaml
from hermes_cli.plugins import get_plugin_manager

home = Path('/sandbox/.hermes')
config = yaml.safe_load((home / 'config.yaml').read_bytes())
manager = get_plugin_manager()
manager.discover_and_load()
rows = [row for row in manager.list_plugins() if row['name'] in {'omh', 'nemoclaw'}]
omh = next(row for row in rows if row['name'] == 'omh')
assert omh['enabled'] and not omh['error'] and omh['tools'] > 0 and omh['hooks'] > 0
assert config['model']['default'] == 'automation-moe'
assert config['model']['api_key'] == '${COMPATIBLE_API_KEY}'
assert config['delegation']['max_concurrent_children'] == 1
assert config.get('memory', {}).get('provider') != 'omh'
assert config['plugins']['entries']['omh']['settings']['dreaming_mode'] == 'off'
assert config['browser']['allow_private_urls'] is False
assert 'homecompute' in config['mcp_servers']
root = Path('/sandbox/.omh')
from omh.plugin_bundle.omh.hermes_delegation import effective_mixture_category_chains, load_model_provider_routes
from omh.coding.parallelism_policy import read_parallelism_policy
from omh.system.paths import OmhPaths
chains = json.loads((root / 'routing/model-chains.json').read_bytes())['categories']
assert len(chains) == 18 and all(value == [{'model': 'automation-moe', 'reasoning_effort': 'medium'}]
                                 for value in chains.values())
profile = json.loads((root / 'setup-profile.json').read_bytes())['parallelism']
assert profile['default_concurrency'] == profile['global_concurrency'] == profile['lane_budget_default'] == 1
effective = effective_mixture_category_chains(root, home)
assert len(effective) == 18 and all(chain == (('automation-moe', 'medium'),) for chain in effective.values())
routes, status = load_model_provider_routes(root)
assert status == 'applied' and routes == {'automation-moe': ('custom', 'automation-moe')}
policy = read_parallelism_policy(OmhPaths(root, home))
assert not policy['ignored_keys']
assert policy['default_concurrency'] == policy['global_concurrency'] == policy['lane_budget_default'] == 1
print(json.dumps({'plugins': rows, 'local_model_categories': len(chains), 'concurrency': 1,
                  'native_memory_preserved': True, 'browser_private_urls_denied': True,
                  'mcp_configuration_present': True, 'dashboard_public_url': config['dashboard']['public_url']}))
