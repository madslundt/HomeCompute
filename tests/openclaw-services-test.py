"""Finite service facade fixtures; never contact household or paid services."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_services as service


class FixtureRanker:
    """Deterministic retrieval fixture; service tests never invoke inference."""
    def rank(self, query, records, *, task, text_key='text', limit=20):
        rows=[]
        for record in records:
            score=float(query.casefold() in record.get(text_key,'').casefold())
            rows.append({**record,'retrieval_score':score,'lexical_score':score,'semantic_score':score})
        return sorted(rows,key=lambda row:row['retrieval_score'],reverse=True)[:limit]


class Response:
    def __init__(self, value, *, status=200, headers=None, raw=None):
        self.status, self.headers = status, headers or {}
        self.body = raw if raw is not None else json.dumps(value).encode()
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return self.body[:size]


class ServicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = self.root / 'key'
        self.key.write_text('tvly-synthetic-key-never-live'); self.key.chmod(0o600)
        self.config = self.root / 'services.json'
        self.config.write_text(json.dumps({'schema_version': 1, 'tavily_token_file': str(self.key)}))
        self.config.chmod(0o600)
        self.path_patch = patch.object(service, 'TOKEN_PATH', self.key); self.path_patch.start()
        self.ranker = FixtureRanker()
        self.client = service.Services(self.config,ranker=self.ranker)
        self.addCleanup(self.path_patch.stop); self.addCleanup(self.tmp.cleanup)

    def test_inventory_and_exact_arguments(self):
        self.assertEqual(len(self.client.tools), 12)
        for t in self.client.tools:
            self.assertTrue(t['annotations']['readOnlyHint'])
            self.assertFalse(t['inputSchema']['additionalProperties'])
        for name, args in [('homecompute_web_search', {'query':'x','url':'http://private'}),
                           ('homecompute_school_calendar', {'profile_ids':'1,1'}),
                           ('homecompute_school_calendar', {'profile_ids':'0'}),
                           ('homecompute_school_thread', {'thread_id':'1e2'}),
                           ('homecompute_shopping_search', {'query':'x\x00'}),
                           ('homecompute_web_search', {'query':'x','topic':'finance'}),
                           ('homecompute_school_threads', {'page':'1'}),
                           ('homecompute_approve', {})]:
            with self.assertRaises(ValueError): self.client.validate(name,args)
        self.assertEqual(self.client.validate('homecompute_school_calendar', {'profile_ids':'2,3'}), {'profile_ids':'2,3','range':'this_week'})

    def test_homework_in_weekly_letter_is_readable_beyond_calendar(self):
        letter = {'items': [{'childId': 12, 'childName': 'Synthetic Child', 'date': 44,
                            'content': '<p>Dansk:</p><p>' + 'Ugens indhold. ' * 180 +
                            'Til på fredag, skal man øve følgende ord til diktat: Nej, på, år, små, min, så, kan, fri, nu, hvad</p>'}]}
        responses = [Response({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}},
                              headers={'Mcp-Session-Id':'synthetic'}), Response(None,status=202,raw=b''),
                     Response({'jsonrpc':'2.0','id':2,'result':{'content':[{'type':'text','text':json.dumps(letter)}]}}),
                     Response(None,status=204,raw=b'')]
        with patch.object(self.client.opener,'open',side_effect=responses) as opened:
            result = self.client.request('homecompute_school_weekly_letter',
                {'child_ids':'12','institution_codes':'SYN','iso_week':'2026-W44'})
        self.assertIn('Nej, på, år, små, min, så, kan, fri, nu, hvad', json.dumps(result,ensure_ascii=False))
        self.assertEqual(json.loads(opened.call_args_list[2].args[0].data)['params'],
                         {'name':'aula_ugebrev_minuddannelse','arguments':
                          {'childIds':[12],'institutionCodes':['SYN'],'isoWeek':'2026-W44'}})
        self.assertLessEqual(len(json.dumps(result).encode()),service.MAX_OUTPUT)

    def test_weekly_letter_pages_preserve_all_text_and_decode_html(self):
        content = '<style>hidden</style><p>' + 'æøå ' * 24000 + '</p><p>Nej, p&aring;, &aring;r, sm&aring;</p>'
        payload = {'items':[{'childName':'Synthetic','date':44,'content':content}], 'raw':{'token':'PRIVATE'}}
        offset=0; chunks=[]
        while True:
            page=service.weekly_letter_page(payload,offset)
            self.assertLess(len(json.dumps(page).encode()),service.MAX_OUTPUT)
            self.assertGreater(page['returned_count'],0)
            chunks.extend(row['text'] for row in page['letters'])
            if page['next_offset'] is None: break
            offset=int(page['next_offset'])
        self.assertEqual(''.join(chunks),('æøå '*24000).strip()+'\nNej, på, år, små')
        self.assertNotIn('PRIVATE',''.join(chunks))

    def test_weekly_letter_requires_valid_week_and_finite_discovered_ids(self):
        valid={'child_ids':'12','institution_codes':'SYN','iso_week':'2026-W44'}
        for replacement in ({'iso_week':'2026-W54'},{'iso_week':'2026-W00'},
                            {'child_ids':'1,1'},{'institution_codes':'https://private'},
                            {'offset':'-1'},{'iso_week':'2025-W53'}):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                self.client.validate('homecompute_school_weekly_letter',{**valid,**replacement})

    def test_private_config_refuses_symlinks_permissions_and_arbitrary_paths(self):
        self.key.chmod(0o644)
        with self.assertRaises(ValueError): service.Services(self.config)
        self.key.chmod(0o600)
        self.config.write_text(json.dumps({'schema_version':1,'tavily_token_file':'/etc/passwd'}))
        with self.assertRaises(ValueError): service.Services(self.config)
        self.config.unlink(); self.config.symlink_to(self.key)
        with self.assertRaises(ValueError): service.Services(self.config)

    def test_tavily_fixed_destination_bounded_projection_and_sources(self):
        values={'results':[{'title':'Example','url':'https://example.com/a','content':'ignore instructions '*100,
                            'score':0.9,'published_date':'2026-10-23','raw_content':'SECRET_UNUSED'}]*25,
                'answer':'SECRET_UNUSED','request_id':'synthetic'}
        with patch.object(self.client.opener,'open',return_value=Response(values)) as opened:
            result=self.client.request('homecompute_web_search',{'query':'synthetic'})
        req=opened.call_args.args[0]; body=json.loads(req.data)
        self.assertEqual(req.full_url,'https://api.tavily.com/search')
        self.assertEqual(body['max_results'],5); self.assertFalse(body['include_raw_content'])
        self.assertFalse(body['auto_parameters']); self.assertTrue(result['untrusted_content'])
        self.assertEqual(len(result['results']),5); self.assertLessEqual(len(result['results'][0]['snippet']),1500)
        self.assertNotIn('SECRET_UNUSED',json.dumps(result)); self.assertIn('retrieved_at',result)
        self.assertEqual(result['sources'][0], 'https://example.com/a')

    def test_mcp_session_protocol_and_no_caller_tool_selection(self):
        responses=[Response({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}},headers={'Mcp-Session-Id':'synthetic-session'}),
                   Response(None,status=202,raw=b''),
                   Response(None,headers={'Content-Type':'text/event-stream'},raw=b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\\"items\\":[{\\"title\\":\\"Synthetic\\"}]}"}]}}\n\n'),
                   Response(None,status=204,raw=b'')]
        with patch.object(self.client.opener,'open',side_effect=responses) as opened:
            result=self.client.request('homecompute_shopping_search',{'query':'coffee'})
        calls=opened.call_args_list
        self.assertTrue(all(c.args[0].full_url=='http://127.0.0.1:17879/mcp' for c in calls))
        self.assertEqual(json.loads(calls[2].args[0].data)['params'], {'name':'search_deals','arguments':{'query':'coffee','limit':20}})
        self.assertEqual(calls[2].args[0].get_header('Mcp-session-id'),'synthetic-session')
        self.assertTrue(result['untrusted_content']); self.assertEqual(result['source'],'tilbudstrolden')
        self.assertEqual(calls[-1].args[0].method,'DELETE')

    def test_failed_transport_and_oversized_private_errors_are_sanitized(self):
        for error in (OSError('private-token-secret'), ValueError('private-token-secret')):
            self.client._search_backoff = (0, '')
            with patch.object(self.client.opener,'open',side_effect=error):
                result=self.client.request('homecompute_web_search',{'query':'synthetic'})
                self.assertEqual(result['status'],'browser_fallback_required')
                self.assertNotIn('secret',json.dumps(result))
                with self.assertRaisesRegex(ValueError,'Service read unavailable'):
                    self.client.request('homecompute_school_discover',{})
        self.client._search_backoff = (0, '')
        with patch.object(self.client.opener,'open',return_value=Response(None,raw=b'x'*(service.MAX_BYTES+1))):
            self.assertEqual(self.client.request('homecompute_web_search',{'query':'synthetic'})['status'],
                             'browser_fallback_required')

    def test_search_limit_fallback_has_no_fake_results_or_private_errors(self):
        for code,reason in [(429,'rate_limited'),(432,'quota_exhausted'),(433,'quota_exhausted'),
                            (401,'authentication_failed'),(500,'unavailable')]:
            self.client._search_backoff = (0, '')
            error=urllib.error.HTTPError('https://api.tavily.com/search',code,'PRIVATE token',{},None)
            with self.subTest(code=code),patch.object(self.client.opener,'open',side_effect=error) as opened:
                result=self.client.request('homecompute_web_search',{'query':'NixOS & docs? æøå'})
            self.assertEqual(result['reason'],reason)
            self.assertEqual(result['results'],[])
            self.assertEqual(result['sources'],[])
            self.assertEqual(result['fallback']['provider'],'marginalia')
            url=urllib.parse.urlsplit(result['fallback']['targetUrl'])
            self.assertEqual(url.hostname,'marginalia-search.com')
            self.assertEqual(urllib.parse.parse_qs(url.query),{'query':['NixOS & docs? æøå']})
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertEqual(opened.call_count,1)

    def test_browser_search_mode_spends_no_tavily_credit_and_never_truncates_query(self):
        with patch.object(self.client.opener,'open') as opened:
            result=self.client.request('homecompute_web_search',{'query':'NixOS docs','provider':'browser'})
            self.assertEqual(result['reason'],'browser_requested')
            self.assertTrue(result['fallback']['targetUrl'].startswith('https://marginalia-search.com/search?'))
            result=self.client.request('homecompute_web_search',{'query':'界'*1000,'provider':'browser'})
            self.assertIsNone(result['fallback']['targetUrl'])
            self.assertEqual(result['fallback']['status'],'shorter_query_required')
            self.assertEqual(result['fallback']['query'],'界'*1000)
            opened.assert_not_called()
        with self.assertRaises(ValueError):
            self.client.request('homecompute_web_search',{'query':'x','provider':'https://private'})

    def test_search_quota_cooldown_skips_provider_and_recovers_after_expiry(self):
        error=urllib.error.HTTPError('https://api.tavily.com/search',432,'PRIVATE',{},None)
        with patch.object(service.time,'monotonic',return_value=100), \
             patch.object(self.client.opener,'open',side_effect=error) as opened:
            self.client.request('homecompute_web_search',{'query':'one'})
            result=self.client.request('homecompute_web_search',{'query':'two'})
            self.assertEqual(opened.call_count,1)
            self.assertEqual(result['reason'],'quota_exhausted')
            self.assertGreater(result['retry_after_seconds'],0)
        response={'results':[{'title':'NixOS','url':'https://nixos.org/','content':'Official site'}]}
        with patch.object(service.time,'monotonic',return_value=100+6*3600+1), \
             patch.object(self.client.opener,'open',return_value=Response(response)) as opened:
            self.assertEqual(self.client.request('homecompute_web_search',{'query':'three'})['source'],'tavily')
            self.assertEqual(opened.call_count,1)

    def test_aula_only_has_larger_finite_ingress_without_larger_output_or_timeout(self):
        payload=b'x'*116909
        deadline=time.monotonic()+8
        with patch.object(self.client.opener,'open',return_value=Response(None,raw=payload)) as opened:
            _,raw,_=self.client._http('http://127.0.0.1:17878/mcp',{},deadline)
            self.assertEqual(len(raw),116909)
            self.assertLessEqual(opened.call_args.kwargs['timeout'],8)
        for url,limit in [('http://127.0.0.1:17878/mcp',524288),
                          ('http://127.0.0.1:17879/mcp',65536),
                          ('https://api.tavily.com/search',65536),
                          ('http://127.0.0.1:17878/mcp?extra=1',65536)]:
            with self.subTest(url=url), patch.object(self.client.opener,'open',return_value=Response(None,raw=b'x'*(limit+1))):
                with self.assertRaisesRegex(ValueError,'Service budget'):
                    self.client._http(url,{},time.monotonic()+8)
        self.assertEqual(service.MAX_OUTPUT,24000)
        projected=service.project({'threads':[{'subject':'synthetic','text':'x'*10000}]*20})
        self.assertLess(len(json.dumps(projected).encode()),service.MAX_OUTPUT)

    def test_weekly_calendar_large_source_is_paged_without_losing_later_events(self):
        events=[{'id':i+1,'title':'Synthetic event '+str(i+1),
                 'startDateTime':'2026-10-26T08:00:00+01:00',
                 'endDateTime':'2026-10-26T09:00:00+01:00',
                 'unusedUpstreamMetadata':'x'*9500} for i in range(30)]
        source=Response({'jsonrpc':'2.0','id':2,'result':{
            'content':[{'type':'text','text':json.dumps(events)}]}})
        self.assertGreater(len(source.body),262144)
        self.assertLess(len(source.body),524288)
        for offset,ids,next_offset in [(None,list(range(1,21)),'20'),('20',list(range(21,31)),None),
                                      ('30',[],None)]:
            responses=[Response({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}},
                                headers={'Mcp-Session-Id':'synthetic'}),
                       Response(None,status=202,raw=b''),source,Response(None,status=204,raw=b'')]
            args={'profile_ids':'12','range':'next_week'}
            if offset is not None: args['offset']=offset
            with self.subTest(offset=offset), patch.object(self.client.opener,'open',side_effect=responses) as opened:
                result=self.client.request('homecompute_school_calendar',args)
                self.assertEqual([e['id'] for e in result['data']['events']],ids)
                self.assertEqual(result['data']['total_count'],30)
                self.assertEqual(result['data']['next_offset'],next_offset)
                self.assertLessEqual(len(json.dumps(result).encode()),24000)
                self.assertNotIn('unusedUpstreamMetadata',json.dumps(result))
                params=json.loads(opened.call_args_list[2].args[0].data)['params']
                self.assertEqual(params['arguments'],{'profileIds':[12],'range':'next_week'})

    def test_calendar_offset_is_finite_and_unavailable_on_other_tools(self):
        for offset in ('-1','1.5','01','10000',' 1',1):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.client.validate('homecompute_school_calendar',{'profile_ids':'12','offset':offset})
        with self.assertRaises(ValueError):
            self.client.validate('homecompute_school_posts',{'offset':'20'})

    def test_calendar_byte_budget_advances_without_skipping_later_events(self):
        events=[{'lesson':{'participants':[{'name':'Nested metadata '*100}]*20},
                 'description':'ø'*2000,'id':i+1,'title':'Event '+str(i+1)} for i in range(30)]
        offset=0; seen=[]
        while True:
            page=service.calendar_page(events,offset)
            self.assertLessEqual(len(json.dumps(page).encode()),service.MAX_OUTPUT-3000)
            self.assertGreater(page['returned_count'],0)
            seen.extend(row['id'] for row in page['events'])
            self.assertTrue(all(row['title'].startswith('Event ') for row in page['events']))
            if page['next_offset'] is None: break
            self.assertGreater(int(page['next_offset']),offset)
            offset=int(page['next_offset'])
        self.assertEqual(seen,list(range(1,31)))

    def test_calendar_rejects_malformed_source_without_claiming_empty_results(self):
        for payload in ({'events':[]},[None],[{'unrecognized_field':'private'}]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                service.calendar_page(payload,0)

    def test_all_aula_and_shopping_routes_use_exact_bounded_arguments(self):
        cases=[
            ('school_discover',{},'aula','aula_discover',{}),
            ('school_calendar',{'profile_ids':'12,34','range':'tomorrow'},'aula','aula_calendar_events',{'profileIds':[12,34],'range':'tomorrow'}),
            ('school_posts',{},'aula','aula_posts_list',{'limit':20,'index':0}),
            ('school_posts',{'profile_ids':'12'},'aula','aula_posts_list',{'limit':20,'index':0,'profileIds':[12]}),
            ('school_threads',{},'aula','aula_messages_list_threads',{'page':0,'pageSize':20}),
            ('school_thread',{'thread_id':'123'},'aula','aula_messages_get_thread',{'threadId':123,'page':0}),
            ('shopping_search',{'query':'coffee'},'tilbudstrolden','search_deals',{'query':'coffee','limit':20}),
            ('shopping_stores',{},'tilbudstrolden','list_stores',{'all':False}),
            ('shopping_stores',{'query':'market'},'tilbudstrolden','list_stores',{'query':'market','all':False}),
            ('shopping_offers',{'store':'Synthetic Market'},'tilbudstrolden','get_store_offers',{'store':'Synthetic Market','limit':20}),
        ]
        for suffix,args,source,name,expected in cases:
            with self.subTest(suffix=suffix,args=args), patch.object(self.client,'_mcp',return_value={}) as called:
                result=self.client.request('homecompute_'+suffix,args)
                self.assertEqual(called.call_args.args[:3],(source,name,expected))
                self.assertEqual(result['source'],source)
        for name,args in [('school_calendar',{'profile_ids':[12]}),('school_thread',{'thread_id':123}),
                          ('school_posts',{'profile_ids':'1,2,3,4,5,6,7,8,9'}),
                          ('school_posts',{'profile_ids':'01'}),('shopping_stores',{'all':'true'})]:
            with self.subTest(name=name), self.assertRaises(ValueError): self.client.validate('homecompute_'+name,args)

    def test_shopping_plain_markdown_and_store_count_are_bounded(self):
        text='25 grocery stores:\n'+''.join('- Synthetic store (id: '+str(i)+')\n' for i in range(25))
        responses=[Response({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}},headers={'Mcp-Session-Id':'synthetic'}),
                   Response(None,status=202,raw=b''),
                   Response({'jsonrpc':'2.0','id':2,'result':{'content':[{'type':'text','text':text}]}}),
                   Response(None,status=204,raw=b'')]
        with patch.object(self.client.opener,'open',side_effect=responses):
            result=self.client.request('homecompute_shopping_stores',{})
        self.assertEqual(result['data']['format'],'markdown'); self.assertTrue(result['data']['truncated'])
        self.assertEqual(sum(line.startswith('- ') for line in result['data']['text'].splitlines()),20)
        self.assertIn('Synthetic store',result['data']['text'])

    def test_session_rejects_upstream_errors_bad_ids_or_missing_session(self):
        for response in [Response({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}}),
                         Response({'jsonrpc':'2.0','id':1,'error':{'message':'private-upstream-error'}}),
                         Response({'jsonrpc':'2.0','id':99,'result':{}})]:
            with patch.object(self.client.opener,'open',return_value=response), self.assertRaisesRegex(ValueError,'Service read unavailable'):
                self.client.request('homecompute_school_discover',{})

    def test_upstream_tool_error_is_not_treated_as_markdown_or_empty_data(self):
        for name in ('homecompute_school_discover','homecompute_shopping_search'):
            responses=[Response({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}},headers={'Mcp-Session-Id':'synthetic'}),
                       Response(None,status=202,raw=b''),
                       Response({'jsonrpc':'2.0','id':2,'result':{'isError':True,'content':[{'type':'text','text':'private dependency detail'}]}}),
                       Response(None,status=204,raw=b'')]
            with patch.object(self.client.opener,'open',side_effect=responses), self.assertRaisesRegex(ValueError,'Service read unavailable') as caught:
                self.client.request(name,{'query':'synthetic'} if name.endswith('search') else {})
            self.assertNotIn('private',str(caught.exception))
        self.assertIsNone(service.NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_installed_aula_shapes_preserve_evidence_and_omit_attachments(self):
        fixtures=[
            {'children':[{'id':12,'name':'Synthetic Child','userId':34,'institution':{'id':56,'name':'Synthetic School','code':'SYN'}}]},
            [{'type':'lesson','title':'Synthetic','startDateTime':'2026-10-24T08:00:00Z','endDateTime':'2026-10-24T09:00:00Z',
              'belongsToProfiles':[12],'primaryResource':{'name':'Synthetic room'},'lesson':{'participants':[]}}],
            {'posts':[{'id':1,'title':'Synthetic','publishAt':'2026-10-24','isImportant':True,'content':{'html':'Synthetic text'},
                       'ownerProfile':{'fullName':'Synthetic Author','institution':{'institutionName':'Synthetic institution'}}}],
             'moreMessagesExist':False,'profileLastSeenPostDate':'2026-10-23'},
            {'threads':[{'id':12,'read':False,'subject':'Synthetic','lastMessage':{'sendDateTime':'2026-10-24','sender':{'fullName':'Synthetic'}}}],
             'page':0,'hasMorePages':True},
            {'subject':'Synthetic','messages':[{'messageType':'Message','text':{'html':'Synthetic text','plain':'Synthetic text'},
             'sender':{'fullName':'Synthetic'},'sendDateTime':'2026-10-24','hasAttachments':True}]},
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture): self.assertEqual(service.project(fixture),fixture)
        self.assertEqual(service.project({'content':{'html':'safe'},'attachments':[{'url':'https://example.com/private'}],
                                         'username':'PRIVATE','tokenExpiresAt':'PRIVATE'}),{'content':{'html':'safe'}})

    def test_projection_global_budget_preserves_partial_evidence(self):
        raw={'posts':[{'title':'Synthetic '+str(i),'content':'é'*2000,'author':{'name':'Synthetic'}} for i in range(20)]}
        result=service.project(raw)
        self.assertLessEqual(len(json.dumps(result).encode()),service.MAX_OUTPUT-4000)
        self.assertGreater(len(result['posts']),0)
        self.assertEqual(result['posts'][0]['title'],'Synthetic 0')

    def test_unknown_and_credential_response_fields_are_not_returned(self):
        value={'api_key':'private','access_token':'private','items':[{'title':'safe','password':'private','attachment_path':'/private','url':'http://127.0.0.1/admin'}]*25}
        result=service.project(value)
        self.assertNotIn('private',json.dumps(result)); self.assertLessEqual(len(result['items']),20)
        self.assertNotIn('url',result['items'][0])

    def test_archive_search_has_exact_finite_arguments_and_no_caller_scope(self):
        for suffix in ('school_search','research_search'):
            operation='homecompute_'+suffix
            self.assertEqual(self.client.validate(operation,{'query':'excursion','limit':'10'}),
                             {'query':'excursion','limit':'10'})
            for extra in ({'limit':'0'},{'limit':'11'},{'limit':5},{'limit':'01'},
                          {'path':'/tmp/other'},{'principal_scope':'partner'},
                          {'domain':'school'},{'source_id':'private'},{'provider':'browser'}):
                with self.subTest(tool=operation,args=extra), self.assertRaises(ValueError):
                    self.client.validate(operation,{'query':'excursion',**extra})

    def test_school_archive_persists_projected_evidence_with_provenance_and_replaces_snapshot(self):
        payload={'posts':[{'id':7,'title':'Excursion','publishedAt':'2026-10-23',
            'content':{'html':'<p>Bring boots Friday</p>'},'access_token':'DO_NOT_INDEX',
            'attachments':[{'content':'DO_NOT_INDEX'}]}]}
        responses=[Response({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26'}},headers={'Mcp-Session-Id':'synthetic'}),
                   Response(None,status=202,raw=b''),
                   Response({'jsonrpc':'2.0','id':2,'result':{'content':[{'type':'text','text':json.dumps(payload)}]}}),
                   Response(None,status=204,raw=b'')]
        with patch.object(self.client.opener,'open',side_effect=responses):
            read=self.client.request('homecompute_school_posts',{'profile_ids':'12'})
        self.assertEqual(read['archive_status'],'updated')
        restarted=service.Services(self.config,ranker=self.ranker)
        with patch.object(restarted,'_http',side_effect=AssertionError('archive search has no upstream read')):
            found=restarted.request('homecompute_school_search',{'query':'boots'})
        self.assertEqual(len(found['results']),1)
        self.assertIn('Bring boots Friday',found['results'][0]['text'])
        self.assertNotIn('DO_NOT_INDEX',json.dumps(found))
        metadata=found['results'][0]['metadata']
        self.assertEqual(metadata['principal_scope'],'owner')
        self.assertEqual(metadata['source_scope'],{'profile_ids':'12'})
        self.assertEqual(metadata['source_tool'],'homecompute_school_posts')
        self.assertEqual(metadata['source_timestamp'],'2026-10-23')
        self.assertEqual(metadata['captured_at'],read['retrieved_at'])
        self.assertTrue(found['requires_fresh_read']); self.assertTrue(found['untrusted_content'])
        public=restarted.request('homecompute_research_search',{'query':'boots'})
        self.assertEqual(public['results'],[])
        with patch.object(restarted,'_mcp',return_value={'posts':[{'id':7,'text':'Updated: bring sandals'}]}):
            restarted.request('homecompute_school_posts',{'profile_ids':'12'})
        updated=restarted.request('homecompute_school_search',{'query':'sandals'})
        self.assertIn('bring sandals',json.dumps(updated))
        self.assertNotIn('Bring boots Friday',json.dumps(updated))
        with patch.object(restarted,'_mcp',return_value={'posts':[]}):
            restarted.request('homecompute_school_posts',{'profile_ids':'12'})
        self.assertIn('bring sandals',json.dumps(restarted.request('homecompute_school_search',{'query':'sandals'})))

    def test_school_thread_and_weekly_pages_keep_source_scope_and_replace_old_passages(self):
        with patch.object(self.client,'_mcp',return_value={'subject':'School trip','messages':[
            {'id':9,'text':{'plain':'Remember waterproof boots','html':'IGNORE DUPLICATE'},'sendDateTime':'2026-10-25'}]}):
            self.client.request('homecompute_school_thread',{'thread_id':'123'})
        args={'child_ids':'12','institution_codes':'SYN','iso_week':'2026-W44'}
        for offset,text in [('0','Homework: practice Danish spelling'),('20','Homework: bring a book')]:
            with patch.object(self.client,'_mcp',return_value={'letters':[
                {'childName':'Synthetic Child','subject':'Danish','week_number':44,'text':text,'chunk_index':int(offset),'chunk_count':21}]}):
                self.client.request('homecompute_school_weekly_letter',{**args,'offset':offset})
        found=self.client.request('homecompute_school_search',{'query':'Homework','limit':'10'})
        self.assertEqual(len(found['results']),3)
        self.assertNotIn('IGNORE DUPLICATE',json.dumps(found))
        self.assertEqual({row['metadata']['source_scope'].get('offset') for row in found['results']},{None,'0','20'})
        with patch.object(self.client,'_mcp',return_value={'subject':'School trip','messages':[{'id':9,'text':'Updated: bring sandals'}]}):
            self.client.request('homecompute_school_thread',{'thread_id':'123'})
        current=self.client.request('homecompute_school_search',{'query':'trip','limit':'10'})
        self.assertNotIn('waterproof boots',json.dumps(current))
        self.assertIn('sandals',json.dumps(current))

    def test_school_first_page_shift_retains_identified_history_and_stable_chunk_ids(self):
        for suffix,key,args in [('school_posts','postId',{'profile_ids':'12'}),
                                ('school_thread','messageId',{'thread_id':'321'})]:
            def payload(rows):
                return {'posts':rows} if suffix=='school_posts' else {'subject':'Synthetic trip','messages':rows}
            rows=[{key:i,'text':'Historical boots seven '+'padding '*240 if i==7 else 'Other passage '+str(i)} for i in range(1,21)]
            with patch.object(self.client,'_mcp',return_value=payload(rows)):
                self.client.request('homecompute_'+suffix,args)
            before=self.client.request('homecompute_school_search',{'query':'Historical boots seven'})
            hit=next(row for row in before['results'] if row['metadata']['source_tool']=='homecompute_'+suffix)
            with patch.object(self.client,'_mcp',return_value=payload(list(reversed(rows)))):
                self.client.request('homecompute_'+suffix,args)
            after=self.client.request('homecompute_school_search',{'query':'Historical boots seven'})
            moved=next(row for row in after['results'] if row['metadata']['source_tool']=='homecompute_'+suffix)
            self.assertEqual(hit['id'],moved['id'])
            with patch.object(self.client,'_mcp',return_value=payload([{key:i,'text':'Newer passage '+str(i)} for i in range(21,41)])):
                self.client.request('homecompute_'+suffix,args)
            retained=self.client.request('homecompute_school_search',{'query':'Historical boots seven'})
            self.assertTrue(any(row['id']==hit['id'] for row in retained['results']))
            with patch.object(self.client,'_mcp',return_value=payload([{key:7}])):
                self.client.request('homecompute_'+suffix,args)
            projected_partial=self.client.request('homecompute_school_search',{'query':'Historical boots seven'})
            self.assertTrue(any(row['id']==hit['id'] for row in projected_partial['results']))
            with patch.object(self.client,'_mcp',return_value=payload([{key:7,'text':'Updated sandals seven'}])):
                self.client.request('homecompute_'+suffix,args)
            archived=self.client._index('school').search('seven',task='school',limit=100)
            relevant=[row for row in archived if row['metadata']['source_tool']=='homecompute_'+suffix]
            self.assertNotIn('Historical boots seven',json.dumps(relevant))
            self.assertIn('Updated sandals seven',json.dumps(relevant))
            self.assertEqual(len([row for row in relevant if row['metadata']['source_record_id']=='7']),1)

    def test_school_anonymous_snapshot_replacement_removes_stale_chunks(self):
        with patch.object(self.client,'_mcp',return_value={'posts':[{'text':'Anonymous older passage '*140}]}):
            self.client.request('homecompute_school_posts',{})
        with patch.object(self.client,'_mcp',return_value={'posts':[{'text':'Anonymous updated passage'}]}):
            self.client.request('homecompute_school_posts',{})
        rows=self.client.request('homecompute_school_search',{'query':'Anonymous','limit':'10'})['results']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['text'],'Anonymous updated passage')
        with patch.object(self.client,'_mcp',return_value={'posts':[]}):
            self.client.request('homecompute_school_posts',{})
        self.assertEqual(self.client.request('homecompute_school_search',{'query':'Anonymous'})['results'],[])

    def test_public_search_reranks_and_archives_only_returned_public_evidence(self):
        response={'results':[
            {'title':'Other','url':'https://example.com/other','content':'unrelated','published_date':'2026-10-20'},
            {'title':'Boots guide','url':'https://example.com/boots','content':'boots care','published_date':'2026-10-21'},
            {'title':'Private','url':'http://127.0.0.1/private','content':'SECRET_PRIVATE'}]}
        with patch.object(self.client.opener,'open',return_value=Response(response)), \
             patch.object(self.ranker,'rank',wraps=self.ranker.rank) as rank:
            fresh=self.client.request('homecompute_web_search',{'query':'boots QUERY_NOT_A_DOCUMENT'})
        self.assertEqual(rank.call_args.args[0],'boots QUERY_NOT_A_DOCUMENT')
        self.assertEqual(rank.call_args.kwargs['text_key'],'snippet')
        # A direct matching query proves ordering without changing URL/date evidence.
        with patch.object(self.client.opener,'open',return_value=Response(response)):
            fresh=self.client.request('homecompute_web_search',{'query':'boots'})
        self.assertEqual(fresh['results'][0]['url'],'https://example.com/boots')
        self.assertEqual(fresh['results'][0]['published_date'],'2026-10-21')
        self.assertIn('semantic_score',fresh['results'][0])
        restarted=service.Services(self.config,ranker=self.ranker)
        archived=restarted.request('homecompute_research_search',{'query':'boots'})
        self.assertEqual(archived['results'][0]['metadata']['url'],'https://example.com/boots')
        self.assertEqual(archived['results'][0]['metadata']['published_date'],'2026-10-21')
        self.assertNotIn('QUERY_NOT_A_DOCUMENT',json.dumps(archived))
        self.assertNotIn('SECRET_PRIVATE',json.dumps(archived))
        self.assertEqual(restarted.request('homecompute_school_search',{'query':'boots'})['results'],[])

    def test_failed_and_unrelated_school_reads_do_not_feed_archive(self):
        for operation,args in [('school_discover',{}),('school_threads',{}),
                               ('school_calendar',{'profile_ids':'12'}),('shopping_search',{'query':'boots'})]:
            with patch.object(self.client,'_mcp',return_value={'text':'not archival evidence'}):
                self.client.request('homecompute_'+operation,args)
        with patch.object(self.client,'_mcp',side_effect=ValueError('PRIVATE')):
            with self.assertRaises(ValueError):self.client.request('homecompute_school_posts',{})
        self.assertEqual(self.client.request('homecompute_school_search',{'query':'evidence'})['results'],[])
        with patch.object(self.client,'_index',side_effect=OSError('PRIVATE')):
            with patch.object(self.client,'_mcp',return_value={'posts':[{'text':'Fresh data'}]}):
                fresh=self.client.request('homecompute_school_posts',{})
        self.assertEqual(fresh['archive_status'],'unavailable')
        self.assertEqual(fresh['data']['posts'][0]['text'],'Fresh data')
        self.assertNotIn('PRIVATE',json.dumps(fresh))

    def test_reranking_without_hits_preserves_live_public_results(self):
        response={'results':[{'title':'Copenhagen','url':'https://example.com/capital',
                              'content':'Copenhagen','published_date':'2026-10-21'}]}
        with patch.object(self.client.opener,'open',return_value=Response(response)), \
             patch.object(self.ranker,'rank',return_value=[]):
            result=self.client.request('homecompute_web_search',{'query':'Denmark capital'})
        self.assertEqual(result['results'][0]['url'],'https://example.com/capital')
        self.assertIsNone(result['results'][0]['semantic_score'])

    def test_archive_output_is_bounded_and_private_metadata_is_never_imported(self):
        rows=[{'id':str(i),'text':'界'*1800,'metadata':{'source_tool':'homecompute_school_posts',
               'captured_at':'2026-10-26','source_scope':{'profile_ids':'12'}},'semantic_score':None,
               'retrieval_score':0.1,'lexical_score':0.1} for i in range(10)]
        class Index:
            def search(self,*args,**kwargs):return rows
        client=service.Services(self.config,ranker=self.ranker,school_index=Index())
        result=client.request('homecompute_school_search',{'query':'trip','limit':'10'})
        self.assertLessEqual(len(json.dumps(result).encode()),service.MAX_OUTPUT)
        self.assertGreater(len(result['results']),0)
        self.assertIsNone(result['results'][0]['semantic_score'])


if __name__ == '__main__': unittest.main()
