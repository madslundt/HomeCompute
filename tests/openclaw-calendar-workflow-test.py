"""Run actual n8n Code-node fixtures without credentials or calendar writes."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from openclaw_calendar_workflow import build, sdk

DIRECTORY=ROOT/'automations/calendar'
ALIASES=['mads','kristina','alvira','nora','family']
CALENDARS={alias:alias+'@synthetic.invalid' for alias in ALIASES}
REQUEST='00000000-0000-4000-8000-000000000001'
ARGS={'calendar':'family','summary':'Synthetic','start':'2026-10-24T14:00:00+02:00',
      'end':'2026-10-24T15:00:00+02:00','all_day':False,'request_id':REQUEST}


def run(file, rows, contexts=None, configured=True):
    source=(DIRECTORY/file).read_text().replace('const configured = false;',f'const configured = {str(configured).lower()};').replace('__CALENDAR_IDS__',json.dumps(CALENDARS))
    harness="""const fs=require('fs');const r=JSON.parse(fs.readFileSync(0,'utf8'));
const input={all:()=>r.rows,first:()=>r.rows[0]};const lookup=()=>({all:()=>r.contexts});
try{const value=new Function('$input','$json','$',r.source)(input,r.rows[0]?.json,lookup);process.stdout.write(JSON.stringify({value}));}
catch(e){process.stdout.write(JSON.stringify({error:'refused'}));}"""
    result=subprocess.run(['node','-e',harness],input=json.dumps({'source':source,'rows':rows,'contexts':contexts}),capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def gate(op,args):
    return run('validate-calendar-request.js',[{'json':{'body':{'operation':op,'arguments':args}}}])


def preflight(contexts,rows):
    return run('check-calendar-preflight.js',[{'json':r} for r in rows],contexts)


class WorkflowTests(unittest.TestCase):
    def test_source_artifact_and_credentials_match_builder_with_no_execution_persistence(self):
        workflow=build()
        self.assertEqual(json.loads((DIRECTORY/'n8n-workflow.json').read_text()),workflow)
        self.assertEqual((DIRECTORY/'n8n-workflow.ts').read_text(),sdk(workflow))
        self.assertEqual(workflow['settings']['saveDataSuccessExecution'],'none')
        self.assertEqual(workflow['settings']['saveDataErrorExecution'],'none')
        self.assertFalse(workflow['settings']['availableInMCP'])
        for node in workflow['nodes']:
            if node['type'].endswith('.httpRequest'):
                self.assertFalse(node['retryOnFail'])
                self.assertEqual(node['parameters']['options']['timeout'],8000)
                self.assertFalse(node['parameters']['options']['redirect']['redirect']['followRedirects'])
                self.assertEqual(node['parameters']['nodeCredentialType'],'googleCalendarOAuth2Api')
                self.assertIs(type(node['parameters']['sendBody']),bool)

    def test_configured_fence_and_finite_inputs(self):
        self.assertIn('error',run('validate-calendar-request.js',[{'json':{'body':{'operation':'list','arguments':{}}}}],configured=False))
        for op,args in [('create',{**ARGS,'attendees':[]}),('create',{**ARGS,'calendar':'work'}),
                        ('create',{**ARGS,'url':'https://evil.invalid'}),('delete',{}),
                        ('update',{'calendar':'family','event_id':'synthetic','expected_etag':'"etag"','request_id':REQUEST}),
                        ('availability',{'calendars':'family,family','start':ARGS['start'],'end':ARGS['end']})]:
            with self.subTest(op=op):
                self.assertIn('error',gate(op,args))

    def test_list_targets_only_five_selected_calendar_ids_and_no_work(self):
        rows=gate('list',{})['value']
        self.assertEqual(len(rows),5)
        self.assertEqual({r['json']['alias'] for r in rows},set(ALIASES))
        self.assertTrue(all('/users/me/calendarList/' in r['json']['request']['url'] for r in rows))
        self.assertTrue(all(r['json']['request']['method']=='GET' for r in rows))

    def test_direct_event_id_is_encoded_no_arbitrary_path(self):
        rows=gate('event',{'calendar':'family','event_id':'synthetic_event'})['value']
        self.assertTrue(rows[0]['json']['request']['url'].endswith('/events/synthetic_event'))
        self.assertIn('error',gate('event',{'calendar':'family','event_id':'../../other'}))

    def test_independent_dst_impossible_date_gap_and_24hour_rejections(self):
        for start in ['2026-03-29T02:30:00+01:00','2026-02-30T14:00:00Z','2026-10-24T24:00:00Z']:
            self.assertIn('error',gate('create',{**ARGS,'start':start}))
        self.assertIn('value',gate('create',{**ARGS,'start':'2026-10-25T02:30:00+02:00','end':'2026-10-25T02:30:00+01:00'}))
        self.assertIn('value',gate('create',{**ARGS,'start':'2026-10-24','end':'2026-10-25','all_day':True}))

    def test_create_preflights_deterministic_id_and_never_accepts_existing_id(self):
        contexts=gate('create',ARGS)['value']
        result=preflight(contexts,[{'statusCode':404,'body':{}}])['value'][0]['json']
        self.assertTrue(result['write'])
        request=result['request']
        self.assertEqual(request['method'],'POST')
        self.assertEqual(request['body']['id'],'hc'+REQUEST.replace('-',''))
        self.assertEqual(request['body']['extendedProperties']['private']['homecompute_request'],REQUEST)
        self.assertTrue(request['url'].endswith('?sendUpdates=none'))
        self.assertNotIn('attendees',request['body'])
        self.assertFalse(preflight(contexts,[{'statusCode':200,'body':{}}])['value'][0]['json']['write'])

    def test_updates_preserve_exact_if_match_and_patch_only_requested_fields(self):
        args={'calendar':'family','event_id':'synthetic','expected_etag':'"etag-one"','request_id':REQUEST,'summary':'Updated'}
        contexts=gate('update',args)['value']
        event={'id':'synthetic','etag':'"etag-one"','organizer':{'self':True},'status':'confirmed',
               'extendedProperties':{'private':{'other':'keep'}},'colorId':'3'}
        result=preflight(contexts,[{'statusCode':200,'body':event}])['value'][0]['json']
        self.assertTrue(result['write'])
        self.assertEqual(result['request']['headers'],{'If-Match':'"etag-one"'})
        self.assertEqual(result['request']['body'],{'summary':'Updated'})
        self.assertEqual(result['request']['method'],'PATCH')
        for change in [{'etag':'"stale"'},{'attendees':[{}]},{'attendeesOmitted':True},{'organizer':{'self':False}},
                       {'recurringEventId':'parent'},{'eventType':'focusTime'},{'recurrence':['RRULE:x']},
                       {'extendedProperties':{'private':{'sourceType':'aula'}}}]:
            with self.subTest(change=list(change)):
                result=preflight(contexts,[{'statusCode':200,'body':{**event,**change}}])['value'][0]['json']
                self.assertFalse(result['write'])

    def test_empty_page_next_page_token_remains_truncated(self):
        contexts=gate('events',{'calendar':'family','start':ARGS['start'],'end':ARGS['end']})['value']
        result=preflight(contexts,[{'statusCode':200,'body':{'items':[],'nextPageToken':'private-token'}}])['value'][0]['json']['result']
        self.assertTrue(result['truncated'])
        self.assertNotIn('private-token',json.dumps(result))

    def test_freebusy_errors_and_malformed_intervals_are_unknown_not_free(self):
        contexts=gate('availability',{'calendars':'family','start':ARGS['start'],'end':ARGS['end']})['value']
        identifier=CALENDARS['family']
        for entry in [{'errors':[{'reason':'forbidden'}],'busy':[]},{'busy':[{}]},
                      {'busy':[{'start':ARGS['end'],'end':ARGS['start']}]},{'busy':[{'start':'invalid','end':ARGS['end']}]}]:
            result=preflight(contexts,[{'statusCode':200,'body':{'calendars':{identifier:entry}}}])['value'][0]['json']['result']
            self.assertEqual(result['calendars'][0],{'alias':'family','status':'unknown','busy':[]})
        result=preflight(contexts,[{'statusCode':200,'body':{'calendars':{identifier:{'busy':[]}}}}])['value'][0]['json']['result']
        self.assertEqual(result['calendars'][0]['status'],'known')
        self.assertNotIn(identifier,json.dumps(result))


if __name__=='__main__':
    unittest.main()
