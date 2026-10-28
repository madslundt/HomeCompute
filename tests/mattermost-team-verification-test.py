"""Manifest-driven acceptance probes against an in-memory Mattermost API."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('verify_team',ROOT/'scripts/verify-mattermost-team.py')
verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)
OWNER='o'*26


class Server:
    def __init__(self,accounts):
        self.accounts=accounts;self.posts={};self.counter=0;self.members={OWNER};self.requests=[]
        self.direct_bot=None;self.resume='KEPT_REFERENCE';self.project='p'*26
        self.reply_to_bots=False;self.specialist_fanout=False;self.wrong_thread=False
        self.context={};self.context_fault=None
    def client(self,token):
        server=self
        class API:
            def request(self,method,path,body=None,**_):
                server.requests.append((token,method,path,body))
                if method=='GET':
                    if path.endswith('/members?per_page=200'):return [{'user_id':v} for v in server.members],{}
                    if '/channels/name/' in path:return {'id':server.project,'type':'P','creator_id':OWNER,'team_id':'t'*26},{}
                    if '/members/' in path:
                        user=path.rsplit('/',1)[1]
                        return ({'user_id':user} if user in server.members else None),{}
                    if path.endswith('/thread'):
                        root=path.split('/')[2]
                        return {'posts':{k:v for k,v in server.posts.items() if k==root or v.get('root_id')==root}},{}
                    if '/posts?per_page=' in path:return {'posts':server.posts},{}
                    if path.startswith('/channels/'):return {'id':path.split('/')[2],'team_id':'t'*26,'type':'P','creator_id':OWNER},{}
                    raise AssertionError(path)
                if path.endswith('/members'):
                    server.members.add(body['user_id']);return {'id':body['user_id']},{}
                if path=='/channels/direct':
                    server.direct_bot=body[1];return {'id':'d'*26},{}
                if path!='/posts':raise AssertionError(path)
                server.counter+=1;identifier=f'{server.counter:026d}'
                post={'id':identifier,'create_at':server.counter*1000,'message':body['message'],
                      'root_id':body['root_id'],'user_id':OWNER if token=='owner-session' else next(a['bot_id'] for a in server.accounts if a['token']==token)}
                server.posts[identifier]=post
                account=None
                if body['channel_id']=='d'*26:account=next(a for a in server.accounts if a['bot_id']==server.direct_bot)
                else:
                    account=next((a for a in server.accounts if a['accountId']=='default' and a['bot_id'] in server.members),None)
                if account and (token=='owner-session' or server.reply_to_bots):
                    match=re.search(r'exactly ([A-Z0-9_]+)',body['message'])
                    text=match.group(1) if match else server.resume
                    key=(body['channel_id'],body['root_id'] or identifier)
                    seed=re.search(r'The reference assigned to THIS thread is (CANARY_[A-Z0-9_]+)',body['message'])
                    if seed:server.context[key]=seed.group(1)
                    if 'What context reference' in body['message']:
                        text=server.context.get(key,'NO_REFERENCE')
                        if server.context_fault=='global':text=list(server.context.values())[-1]
                        if server.context_fault=='channel':
                            values=[value for (channel,_),value in server.context.items() if channel==body['channel_id']]
                            text=values[-1] if values else 'NO_REFERENCE'
                        if server.context_fault=='new-root' and not body['root_id']:text=list(server.context.values())[-1]
                    if server.context_fault=='extra' and ('CANARY_' in text or text=='NO_REFERENCE'):text='Reference: '+text
                    reply_id='r'+f'{server.counter:025d}'
                    server.posts[reply_id]={'id':reply_id,'create_at':post['create_at']+1,'message':text,
                                            'root_id':'' if server.wrong_thread else body['root_id'] or identifier,'user_id':account['bot_id']}
                    if server.specialist_fanout and body['channel_id']!='d'*26:
                        server.posts['f'+f'{server.counter:025d}']={**server.posts[reply_id],'id':'f'+f'{server.counter:025d}','user_id':server.accounts[1]['bot_id']}
                return post,{}
        return API()


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        names=['atlas','luna','nova','apollo','orion','vega','ceres','lyra']
        self.accounts=[{'accountId':account,'agentId':role,'username':name,'bot_id':chr(97+i)*26,'token':'synthetic-'+role}
                       for i,((role,account),name) in enumerate(zip(verify.ROLE_ACCOUNTS.items(),names))]
        self.write('credentials.json',{'operator_id':OWNER,'operator_session':'owner-session'})
        self.write('agent-accounts.json',{'accounts':self.accounts})
        self.write('channels.json',{'inbox':'i'*26,'personal':'j'*26,'research':'k'*26,'engineering':'l'*26,'home-operations':'m'*26,'home-assistant':'h'*26})
        self.write('n8n-channels.json',{'base_url':'https://mattermost.home.arpa','channels':{'home':'w'*26,'shopping':'s'*26,'storage':'u'*26,'travel':'v'*26}})
        self.server=Server(self.accounts)
        self.patches=[patch.object(verify,'PRIVATE',self.root),patch.object(verify.bootstrap,'API',side_effect=self.server.client),patch.object(verify.time,'sleep')]
        for p in self.patches:p.start()
        self.addCleanup(self.tmp.cleanup)
        for p in reversed(self.patches):self.addCleanup(p.stop)
    def write(self,name,value):
        path=self.root/name;path.write_text(json.dumps(value));path.chmod(0o600)

    def test_all_eight_dm_roles_use_stable_ids_and_preserve_existing_receipts(self):
        historical=b'{"historical":"keep exact bytes"}'
        (self.root/'acceptance-dm-main.json').write_bytes(historical)
        for role,account in verify.ROLE_ACCOUNTS.items():
            result=verify.run('dm-'+role,'fresh-dm-'+role)
            self.assertTrue(result['passed'])
            saved=json.loads((self.root/('acceptance-fresh-dm-'+role+'.json')).read_text())
            self.assertEqual(saved['account_id'],account)
        self.assertEqual((self.root/'acceptance-dm-main.json').read_bytes(),historical)
        self.assertEqual(len([r for r in self.server.requests if r[2]=='/posts']),8)

    def test_new_role_channels_and_readonly_tools_use_topic_manifest(self):
        for scenario,channel,name in [('shopper','s'*26,'atlas'),('keeper','u'*26,'atlas'),
                                      ('shopping-tool','s'*26,'atlas'),('storage-tool','u'*26,'atlas'),
                                      ('travel','v'*26,'atlas'),('travel-tool','v'*26,'atlas')]:
            self.server.members={OWNER,*[a['bot_id'] for a in self.accounts]}
            self.assertTrue(verify.run(scenario)['passed'])
            sent=[r[3] for r in self.server.requests if r[2]=='/posts'][-1]
            self.assertEqual(sent['channel_id'],channel)
            self.assertTrue(sent['message'].startswith('@'+name+' '))
            self.assertNotIn('@openclaw',sent['message'])

    def test_legacy_project_probes_explicitly_refuse_without_api_or_receipt_changes(self):
        self.write('acceptance-original-project.json',{'complete':True,'engineer_root':'e'*26})
        before=(self.root/'acceptance-original-project.json').read_bytes()
        for scenario in ('project-membership','project-resume'):
            with self.assertRaisesRegex(ValueError,'retired'):
                verify.run(scenario,'fresh-project',previous_label='original-project')
        self.assertEqual(self.server.requests,[])
        self.assertEqual((self.root/'acceptance-original-project.json').read_bytes(),before)

    def test_fresh_labeled_resume_reads_original_account_and_project_history(self):
        self.write('acceptance-first-dm.json',{'complete':True,'account_id':'keeper','replies':[{'text':'KEPT_REFERENCE'}]})
        self.assertTrue(verify.run('dm-resume-keeper','resumed-dm',previous_label='first-dm')['passed'])

    def fast_clock(self):
        counter=iter(range(0,20000,20))
        return patch.object(verify.time,'monotonic',side_effect=lambda:next(counter))

    def test_unmentioned_channel_owner_followups_specialist_mentions_and_bot_silence(self):
        self.server.members.update(a['bot_id'] for a in self.accounts)
        with self.fast_clock(): result=verify.run('channel-coordinator','automatic-channel')
        self.assertTrue(result['passed']);self.assertEqual(result['account_id'],'default')
        self.assertEqual(len(result['checks']),4);self.assertEqual(result['verified_thread_replies'],3)
        posts=[r for r in self.server.requests if r[2]=='/posts']
        self.assertEqual(posts[0][3]['channel_id'],'w'*26)
        self.assertEqual(posts[1][3]['channel_id'],'w'*26)
        self.assertFalse(posts[0][3]['message'].startswith('@'))
        self.assertFalse(posts[1][3]['message'].startswith('@'))
        self.assertEqual(posts[1][3]['root_id'],next(iter(self.server.posts)))
        self.assertTrue(posts[2][3]['message'].startswith('@luna '))
        self.assertEqual(posts[3][0],'synthetic-personal')
        self.assertTrue(posts[3][3]['message'].startswith('@atlas '))
        saved=json.loads((self.root/'acceptance-automatic-channel.json').read_text())
        self.assertTrue(saved['complete']);self.assertNotIn('pending_post',saved)

    def test_context_probe_recalls_three_threads_and_new_root_has_no_reference(self):
        self.server.members.update(a['bot_id'] for a in self.accounts)
        result=verify.run('channel-context','isolated-context')
        self.assertTrue(result['passed']);self.assertEqual(result['posts'],7)
        self.assertEqual(result['verified_thread_replies'],7)
        saved=json.loads((self.root/'acceptance-isolated-context.json').read_text())
        roots=saved['context_roots'];self.assertEqual(len(roots),4)
        self.assertEqual(roots['A1']['channel'],roots['A2']['channel'])
        self.assertNotEqual(roots['A1']['channel'],roots['B1']['channel'])
        references={value['reference'] for key,value in roots.items() if key!='unrelated'}
        self.assertEqual(len(references),3)
        posts=[r[3] for r in self.server.requests if r[2]=='/posts']
        for followup in posts[3:]:
            self.assertTrue(all(code not in followup['message'] for code in references))
        self.assertTrue(all(not row['message'].startswith('@') for row in posts))
        self.assertIn('native inspection',saved['evidence_scope'])
        self.assertFalse(any(r[1]!='GET' and r[2]!='/posts' for r in self.server.requests))

    def test_context_probe_rejects_channel_merge_global_merge_fresh_root_leaks_and_non_code(self):
        for flag in ('channel','global','new-root','extra'):
            self.server=Server(self.accounts);self.server.members.update(a['bot_id'] for a in self.accounts)
            self.server.context_fault=flag
            with patch.object(verify.bootstrap,'API',side_effect=self.server.client),self.fast_clock():
                with self.assertRaises(ValueError):verify.run('channel-context','context-fault-'+flag)
            saved=json.loads((self.root/('acceptance-context-fault-'+flag+'.json')).read_text())
            self.assertFalse(saved['complete'])

    def test_context_channels_must_be_distinct_owner_channels_with_atlas_already_present(self):
        for index,selected in enumerate(('inbox,inbox','../private,inbox','agent-playground,inbox,extra')):
            with self.assertRaises(ValueError):verify.run('channel-context','bad-context-'+str(index),context_channels=selected)
        with self.assertRaisesRegex(ValueError,'already belong'):
            verify.run('channel-context','missing-context-membership')
        self.assertFalse(any(r[2]=='/posts' for r in self.server.requests))

    def test_lost_post_ack_retains_pending_receipt_and_cannot_replay(self):
        self.server.members.update(a['bot_id'] for a in self.accounts)
        factory=self.server.client
        def client(token):
            result=factory(token);original=result.request
            def request(method,path,body=None,**kwargs):
                response=original(method,path,body,**kwargs)
                if method=='POST' and path=='/posts':raise OSError('Lost synthetic acknowledgement')
                return response
            result.request=request
            return result
        with patch.object(verify.bootstrap,'API',side_effect=client):
            with self.assertRaises(OSError):verify.run('channel-coordinator','lost-channel-post')
        retained=json.loads((self.root/'acceptance-lost-channel-post.json').read_text())
        self.assertIn('pending_post',retained);self.assertFalse(retained['complete'])
        requests=len(self.server.requests)
        with self.assertRaisesRegex(ValueError,'retained state'):verify.run('channel-coordinator','lost-channel-post')
        self.assertEqual(len(self.server.requests),requests)

    def test_channel_probe_fails_for_specialist_fanout_wrong_threads_or_bot_trigger(self):
        for flag in ('specialist_fanout','wrong_thread','reply_to_bots'):
            self.server=Server(self.accounts);self.server.members.update(a['bot_id'] for a in self.accounts)
            setattr(self.server,flag,True)
            with patch.object(verify.bootstrap,'API',side_effect=self.server.client),self.fast_clock():
                with self.assertRaises(ValueError):verify.run('channel-coordinator','fault-'+flag.replace('_','-'))
            saved=json.loads((self.root/('acceptance-fault-'+flag.replace('_','-')+'.json')).read_text())
            self.assertFalse(saved['complete'])

    def test_channel_tool_probes_delegate_stable_roles_while_atlas_answers(self):
        self.server.members.update(a['bot_id'] for a in self.accounts)
        for scenario,leaf in [('shopping-tool','shopper'),('storage-tool','keeper'),('travel-tool','travel')]:
            result=verify.run(scenario,'delegated-'+scenario)
            self.assertEqual(result['account_id'],'default')
            message=[r[3]['message'] for r in self.server.requests if r[2]=='/posts'][-1]
            self.assertTrue(message.startswith('@atlas '));self.assertIn('agentId '+leaf,message)
            self.assertIn('sessions_yield',message)

    def test_legacy_specialist_channel_restart_receipt_cannot_false_green(self):
        self.write('acceptance-old-thread.json',{'complete':True,'account_id':'personal','resume_root':'r'*26,'resume_expected':'OLD'})
        with self.assertRaisesRegex(ValueError,'fresh coordinator'):
            verify.run('restart-resume','new-restart',previous_label='old-thread')
        self.assertFalse(any(r[2]=='/posts' for r in self.server.requests))

    def test_malformed_or_cross_account_resume_cannot_send(self):
        self.write('acceptance-first.json',{'complete':True,'account_id':'personal','replies':[{'text':'PRIVATE'}]})
        with self.assertRaises(ValueError):verify.run('dm-resume-keeper','mismatch',previous_label='first')
        self.assertFalse(any(r[2]=='/posts' for r in self.server.requests))
        for bad in ('../escape','x/y','UPPER',''):
            with self.assertRaises(ValueError):verify.probe_path('dm-main',bad)
        with self.assertRaises(ValueError):verify.run('not-a-role')

    def test_existing_probe_refuses_before_api_and_private_topic_is_checked(self):
        self.write('acceptance-dm-main.json',{'historical':True})
        with self.assertRaises(ValueError):verify.run('dm-main')
        self.assertEqual(self.server.requests,[])
        class PublicAPI:
            def request(self,*_):return {'type':'O','creator_id':OWNER},{}
        with self.assertRaises(ValueError):verify.topic_channels({},'shopping',PublicAPI(),OWNER)

    def test_unknown_or_duplicate_manifest_identity_refused(self):
        for key,value in [('accountId','unexpected'),('username','bad name'),('bot_id',self.accounts[0]['bot_id'])]:
            altered=json.loads(json.dumps(self.accounts));altered[-1][key]=value
            self.write('agent-accounts.json',{'accounts':altered})
            with self.assertRaises(ValueError):verify.account_rows()

    def test_role_scenarios_remain_stable_when_every_username_changes(self):
        for i,row in enumerate(self.accounts):row['username']='renamed-'+str(i)
        self.write('agent-accounts.json',{'accounts':self.accounts})
        self.server.members.update(a['bot_id'] for a in self.accounts)
        self.assertTrue(verify.run('shopper')['passed'])
        message=[r[3]['message'] for r in self.server.requests if r[2]=='/posts'][-1]
        self.assertTrue(message.startswith('@renamed-0 '))
        self.assertIn('dm-resume-shopper',verify.SCENARIOS);self.assertIn('dm-resume-keeper',verify.SCENARIOS)
        self.assertIn('dm-resume-travel',verify.SCENARIOS)


if __name__=='__main__':unittest.main()
