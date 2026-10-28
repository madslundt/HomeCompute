"""A complete retained team is selected by hash; automatic recovery stays narrow."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('team_recovery', ROOT / 'scripts/recover-openclaw-team.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class TeamRecoveryTests(unittest.TestCase):
    def config(self):
        return {'models':{'providers':{'inference':{'baseUrl':'https://inference.local/v1','apiKey':'unused'}}},
                'proxy':{'secret':'synthetic-old'},'gateway':{'auth':{'token':'synthetic-old'},'reload':{'mode':'off'}},
                'agents':{'entries':{'main':{'tools':{'allow':['sessions_spawn']}},'cook':{'workspace':'/sandbox/.openclaw/workspace-cook'}}},
                'channels':{'mattermost':{'enabled':True,'token':'synthetic-channel'}},
                'cron':{'enabled':True}, 'plugins':{'allow':['nemoclaw','homecompute-safety'],
                'load':{'paths':['/retained/plugin']},'entries':{'nemoclaw':{'enabled':True},'homecompute-safety':{'enabled':True}},
                'installs':{'homecompute-safety':{'source':'retained'},'nemoclaw':{'source':'old'}}},
                'mcp':{'retained':'opaque-synthetic'},'tts':{'retained':'opaque-synthetic'}}

    def test_full_team_channels_tools_and_integrations_retained_only_native_ownership_changes(self):
        old=self.config(); before=copy.deepcopy(old); native=self.config()
        native['gateway']['auth']['token']='synthetic-new';native['proxy']['secret']='synthetic-new'
        native['plugins']['installs']['nemoclaw']={'source':'native-new'}
        result=m.retained_config(old,native)
        self.assertEqual(old,before)
        for key in ('agents','channels','cron','mcp','tts'):
            self.assertEqual(result[key],old[key])
        self.assertEqual(result['plugins']['load'],old['plugins']['load'])
        self.assertEqual(result['plugins']['allow'],old['plugins']['allow'])
        self.assertEqual(result['gateway']['auth'],native['gateway']['auth'])
        self.assertEqual(result['proxy'],native['proxy'])
        self.assertEqual(result['plugins']['installs']['nemoclaw'],native['plugins']['installs']['nemoclaw'])
        with self.assertRaises(m.BASE.RecoveryRefused):
            m.BASE.validate_safe_config(result)

    def test_startup_quiescence_does_not_remove_credentials_or_change_tools(self):
        original=self.config(); candidate=m.paused(original)
        self.assertFalse(candidate['channels']['mattermost']['enabled'])
        self.assertFalse(candidate['cron']['enabled'])
        self.assertEqual(candidate['channels']['mattermost']['token'],original['channels']['mattermost']['token'])
        self.assertEqual(candidate['agents'],original['agents'])
        self.assertTrue(original['channels']['mattermost']['enabled'])

    def test_managed_inference_and_plugin_required(self):
        for mutation in (lambda c:c['models']['providers']['inference'].update(apiKey='private'),
                         lambda c:c['models']['providers']['inference'].update(baseUrl='https://example.com'),
                         lambda c:c['plugins']['entries']['nemoclaw'].update(enabled=False)):
            old=self.config();mutation(old)
            with self.assertRaises(ValueError):m.retained_config(old,self.config())

    def test_symlinks_must_remain_inside_state_or_fixed_pinned_sdk(self):
        for path,target in [('npm/project/.bin/tool','../tool/bin.js'),('plugin-skills/browser','/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist/browser')]:
            self.assertTrue(m.safe_link(path,target))
        for target in ('/etc/passwd','../../../outside','/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw-evil'):
            self.assertFalse(m.safe_link('agents/main/link',target))

    def test_all_workspaces_and_plugin_dependencies_are_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('agents/cook/session','workspace-cook/memory.md','extensions/safety/index.mjs','credentials/synthetic'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic')
            (root/'openclaw.json').write_text(json.dumps(self.config()))
            hashes=m.complete_hashes(root)
            self.assertIn('workspace-cook/memory.md',hashes)
            self.assertIn('credentials/synthetic',hashes)
            (root/'workspace-cook/memory.md').write_text('changed')
            self.assertNotEqual(hashes,m.complete_hashes(root))
            (root/'agents/cook/escape').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):m.complete_hashes(root)

if __name__=='__main__':unittest.main()
