"""A complete retained team is selected by hash; automatic recovery stays narrow."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('team_recovery', ROOT / 'scripts/recover-openclaw-team.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class TeamRecoveryTests(unittest.TestCase):
    def test_native_mcp_rebind_preserves_other_policy_and_uses_existing_provider_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx=Mock(state=Path(tmp),openshell='/fixed/openshell');ctx.observe.return_value='Ready'
            tx=m.RetainedTransaction(ctx,'a'*64);tx.inspect=Mock()
            original={'version':1,'network_policies':{'retained':{'name':'retained'}}}
            tx.command=Mock(side_effect=lambda args,label,**kwargs:json.dumps({'policy':original}).encode() if label=='read-replacement-base-policy' else b'')
            tx.attach_mcp()
            result=json.loads((Path(tmp)/'retained-mcp-policy.yaml').read_text())
            self.assertEqual(result['network_policies']['retained'],original['network_policies']['retained'])
            endpoint=result['network_policies']['mcp_bridge_homecompute']['endpoints'][0]
            self.assertEqual(endpoint['allowed_ips'],['10.77.20.1'])
            self.assertEqual(endpoint['credential_binding'],{'provider':'agent-openclaw-mcp-homecompute'})
            self.assertTrue(endpoint['mcp']['strict_tool_names'])
            self.assertFalse(endpoint['mcp']['allow_all_known_mcp_methods'])
            calls=[x.args[0] for x in tx.command.call_args_list]
            self.assertTrue(any('attach' in x and 'agent-openclaw-mcp-homecompute' in x for x in calls))
            self.assertFalse(any(x in ('create','update','delete') for args in calls for x in args))
            original['network_policies']['mcp_bridge_homecompute']={'unknown':'requires review'}
            tx.command.reset_mock()
            with self.assertRaises(ValueError):tx.attach_mcp()
            self.assertEqual(tx.command.call_count,1)

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

    def test_additional_speech_provider_survives_native_merge(self):
        old=self.config();native=self.config()
        old['models']['providers']['openai']={'baseUrl':'http://127.0.0.1:18800/speech','apiKey':'synthetic-placeholder'}
        merged=m.retained_config(old,native)
        self.assertEqual(merged['models']['providers']['openai'],old['models']['providers']['openai'])
        native['models']['providers']['other']={}
        with self.assertRaises(ValueError):m.retained_config(old,native)

    def test_restored_private_routes_keep_exact_address_provider_method_and_paths(self):
        routes=json.loads((ROOT/'deploy/openclaw/nemoclaw/retained-service-routes.json').read_text())['network_policies']
        expected={'/v1/embeddings','/v1/audio/transcriptions','/v1/audio/speech'}
        self.assertEqual({x['endpoints'][0]['path'] for x in routes.values()},expected)
        for entry in routes.values():
            self.assertEqual(entry['binaries'],[{'path':'/usr/local/bin/node'}])
            endpoint=entry['endpoints'][0]
            self.assertEqual(endpoint['allowed_ips'],['10.77.20.1'])
            self.assertEqual(endpoint['host'],'homecompute-capabilities.home.arpa')
            self.assertEqual(endpoint['credential_binding'],{'provider':'agent-openclaw-mcp-homecompute'})
            self.assertEqual(endpoint['rules'],[{'allow':{'method':'POST','path':endpoint['path']}}])

    def test_private_service_routes_use_native_policy_without_untrusted_preset_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx=Mock(state=Path(tmp),openshell='/fixed/openshell');ctx.observe.return_value='Ready'
            tx=m.RetainedTransaction(ctx,'a'*64);tx.inspect=Mock()
            tx.command=Mock(side_effect=lambda args,label,**kwargs:json.dumps({'policy':{'version':1,'network_policies':{'retained':{'name':'retained'}}}}).encode() if label=='read-replacement-base-policy' else b'')
            tx.merge_native_policy('retained-service-routes.json','services.yaml')
            policy=json.loads((Path(tmp)/'services.yaml').read_text())
            self.assertEqual(len(policy['network_policies']),4)
            self.assertTrue(all('attach' not in x.args[0] and '--from-file' not in x.args[0] for x in tx.command.call_args_list))

    def test_managed_inference_and_plugin_required(self):
        for mutation in (lambda c:c['models']['providers']['inference'].update(apiKey='private'),
                         lambda c:c['models']['providers']['inference'].update(baseUrl='https://example.com'),
                         lambda c:c['plugins']['entries']['nemoclaw'].update(enabled=False)):
            old=self.config();mutation(old)
            with self.assertRaises(ValueError):m.retained_config(old,self.config())

    def test_native_auth_rotation_accepts_only_fresh_token_and_unchanged_paused_policy(self):
        expected=m.paused(self.config());previous={'token':'old-native-token-1234'}
        actual=copy.deepcopy(expected);actual['gateway']['auth']={'token':'fresh-native-token-1234'}
        self.assertEqual(m.native_auth_rotation(expected,actual,previous),actual['gateway']['auth'])
        for change in (lambda x:x['agents']['entries'].update(extra={}),
                       lambda x:x['channels']['mattermost'].update(enabled=True),
                       lambda x:x['gateway'].update(auth=previous),
                       lambda x:x['gateway'].update(auth={'token':'short'}),
                       lambda x:x['gateway'].update(auth={'token':'fresh-native-token-1234','password':'extra'})):
            value=copy.deepcopy(actual);change(value)
            with self.assertRaises(ValueError):m.native_auth_rotation(expected,value,previous)

    def test_mcp_refresh_accepts_only_same_native_resolver_key_and_preserves_latest_handle(self):
        expected=m.paused(self.config());previous={'token':'old-native-token-1234'}
        expected['mcp']={'servers':{'homecompute':{'url':'https://homecompute-capabilities.home.arpa:19445/mcp','headers':{'Authorization':'Bearer openshell:resolve:env:v123_HOMECOMPUTE_OPENCLAW_MCP_TOKEN'}}}}
        actual=copy.deepcopy(expected);actual['gateway']['auth']={'token':'fresh-native-token-1234'}
        actual['mcp']['servers']['homecompute']['headers']['Authorization']='Bearer openshell:resolve:env:v456_HOMECOMPUTE_OPENCLAW_MCP_TOKEN'
        self.assertEqual(m.native_auth_rotation(expected,actual,previous),actual['gateway']['auth'])
        retained=copy.deepcopy(expected);m.retain_mcp_handle(retained,actual)
        self.assertEqual(retained['mcp'],actual['mcp'])
        self.assertNotEqual(expected['mcp'],actual['mcp'])
        for change in (lambda x:x['mcp']['servers']['homecompute'].update(url='https://foreign.invalid/mcp'),
                       lambda x:x['mcp']['servers']['homecompute']['headers'].update(Extra='unapproved'),
                       lambda x:x['mcp']['servers']['homecompute']['headers'].update(Authorization='Bearer opaque-secret-value'),
                       lambda x:x['mcp']['servers']['homecompute']['headers'].update(Authorization='Bearer openshell:resolve:env:v456_OTHER_KEY')):
            value=copy.deepcopy(actual);change(value)
            with self.assertRaises(ValueError):m.native_auth_rotation(expected,value,previous)

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
