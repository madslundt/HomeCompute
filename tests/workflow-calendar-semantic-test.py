"""Synthetic candidate fixtures: embeddings can only add reviews or withhold create."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from workflow_calendar_semantic import operations
SHARED=(ROOT/'automations/semantic-calendar/candidates.js').read_text()


def run(expression, data):
    code="const fs=require('fs');const data=JSON.parse(fs.readFileSync(0,'utf8'));\n"+SHARED+'\nprocess.stdout.write(JSON.stringify('+expression+'));'
    result=subprocess.run(['node','-e',code],input=json.dumps(data),capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def event(identifier, summary='School social evening', **changes):
    return {'id':identifier,'summary':summary,'status':'confirmed',
            'start':{'dateTime':'2026-10-26T18:00:00+01:00'},'end':{'dateTime':'2026-10-26T20:00:00+01:00'},
            'organizer':{'email':'owner@synthetic.invalid'},**changes}


def row(identifier, **changes):
    return {'entity':'household_one','event':event(identifier),**changes}


def run_node(source, contexts, inputs):
    harness="""const fs=require('fs'),data=JSON.parse(fs.readFileSync(0,'utf8'));
const input={all:()=>data.inputs.map(json=>({json})),first:()=>({json:data.inputs[0]})};
const lookup=name=>({all:()=>data.contexts[name].map(json=>({json})),first:()=>({json:data.contexts[name][0]})});
process.stdout.write(JSON.stringify(new Function('$input','$',data.source)(input,lookup)));
"""
    result=subprocess.run(['node','-e',harness],input=json.dumps({'source':source,'contexts':contexts,'inputs':inputs}),capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def intake_workflow():
    original="const planned=$('Merge existing calendar event').first().json;\nconst saved=$input.first().json;\nreturn [{json:{...planned,ok:true}}];"
    return {'nodes':[{'name':'Merge existing calendar event','position':[0,0],'parameters':{'jsCode':'EXACT SOURCE'}},
      {'name':'Confirm calendar intake','parameters':{'jsCode':original}}, {'name':'Choose calendar write'}],
      'connections':{'Merge existing calendar event':{'main':[[{'node':'Choose calendar write'}]]}}}


class CalendarSemanticTests(unittest.TestCase):
    def test_pairs_are_titles_only_and_work_and_cancellation_never_embedded(self):
        data=[row('a'),row('b'),row('work',work=True),row('canceled',event=event('canceled',status='cancelled'))]
        data[0]['event']['description']='PRIVATE detail must remain local'
        prepared=run('semanticCalendar.prepare(data)',data)
        request=prepared['requests'][0]
        self.assertEqual(len(request['records']),2)
        serialized=json.dumps(request)
        for value in ['PRIVATE','@','household_one','work']:
            self.assertNotIn(value,serialized)
        self.assertTrue(all(record['metadata']['calendar_id']=='g0' for record in request['records']))

    def test_date_role_calendar_owner_time_and_recurrence_are_separate_groups(self):
        original=row('a')
        variants=[row('calendar',entity='household_two'),row('owner',event=event('owner',organizer={'email':'other@synthetic.invalid'})),
          row('date',event=event('date',start={'dateTime':'2026-10-27T18:00:00+01:00'},end={'dateTime':'2026-10-27T20:00:00+01:00'})),
          row('time',event=event('time',start={'dateTime':'2026-10-26T19:00:00+01:00'})),
          row('role',event=event('role','Svar senest School social evening')),
          row('recurring',event=event('recurring',recurringEventId='series')),
          row('allday',event=event('allday',start={'date':'2026-10-26'},end={'date':'2026-10-27'}))]
        for variant in variants:
            with self.subTest(identifier=variant['event']['id']):
                prepared=run('semanticCalendar.prepare(data)',[original,variant])
                self.assertEqual(prepared['requests'][0]['records'],[])

    def test_all_day_and_recurring_candidates_are_advisory_only(self):
        for changes in [{'start':{'date':'2026-10-26'},'end':{'date':'2026-10-27'}},{'recurringEventId':'series'}]:
            rows=[row('a',event=event('a',**changes)),row('b',event=event('b','Class dinner',**changes))]
            result=run("(() => {const p=semanticCalendar.prepare(data);const pairs=semanticCalendar.candidates(p,[{available:true,pairs:[{left_id:'r0',right_id:'r1',score:0.95}]}]);return semanticCalendar.appendReviews({plans:[],reviews:[]},pairs);})()",rows)
            self.assertEqual(result['plans'],[])
            self.assertEqual(result['reviews'][0]['reason'],'semantic_candidate')

    def test_threshold_outage_timeout_and_unknown_response_ids_fall_back(self):
        for response in [{'available':False},{'error':'timeout'},{'available':True,'pairs':[{'left_id':'r0','right_id':'r1','score':0.85}]},
                         {'available':True,'pairs':[{'left_id':'r0','right_id':'unknown','score':0.99}]}]:
            result=run('semanticCalendar.candidates(semanticCalendar.prepare(data.rows),[data.response])',{'rows':[row('a'),row('b')],'response':response})
            self.assertEqual(result,[])

    def test_exact_plans_reviews_and_pair_deduplication_are_preserved(self):
        audit={'plans':[{'entity':'household_one','keep':{'id':'a'},'duplicates':[{'id':'b'}],'patch':{'description':'exact'}}], 'reviews':[]}
        expression="(() => {const p=semanticCalendar.prepare(data.rows),pairs=semanticCalendar.candidates(p,[{available:true,pairs:[{left_id:'r0',right_id:'r1',score:0.99},{left_id:'r1',right_id:'r0',score:0.99}]}]);return semanticCalendar.appendReviews(data.audit,pairs);})()"
        result=run(expression,{'rows':[row('a'),row('b')],'audit':audit})
        self.assertEqual(result,audit)
        audit={'plans':[],'reviews':[{'date':'2026-10-26','entities':['household_one'],'ids':['a','b'],'reason':'ownership'}]}
        result=run(expression,{'rows':[row('a'),row('b')],'audit':audit})
        self.assertEqual(result,audit)

    def test_large_group_covers_cross_block_pairs_and_each_request_is_bounded(self):
        result=run('semanticCalendar.prepare(data).requests',[row(str(index)) for index in range(97)])
        all_pairs=set()
        for request in result:
            ids=[record['id'] for record in request['records']]
            self.assertLessEqual(len(ids),64)
            all_pairs.update(tuple(sorted((a,b))) for a in ids for b in ids if a!=b)
        self.assertEqual(len(all_pairs),97*96//2)

    def test_quiet_review_identity_and_title_survive_reader_order_changes(self):
        expression="(() => {const p=semanticCalendar.prepare(data),pairs=semanticCalendar.candidates(p,[{available:true,pairs:[{left_id:'r0',right_id:'r1',score:0.99}]}]);return semanticCalendar.appendReviews({plans:[],reviews:[]},pairs).reviews;})()"
        rows=[row('a',event=event('a','Class social evening')),row('b',event=event('b','Parent dinner'))]
        self.assertEqual(run(expression,rows),run(expression,list(reversed(rows))))

    def test_intake_exact_action_does_not_embed_and_create_uses_separate_candidate(self):
        planned={'person':'household_one','eventId':'new','summary':'Class dinner','start':'2026-10-26T18:00:00+01:00','end':'2026-10-26T20:00:00+01:00','allDay':False,'action':'noop'}
        for action in ['noop','update']:
            planned['action']=action
            result=run('semanticCalendar.prepareIntake(data.planned,data.existing)',{'planned':planned,'existing':[event('a')]})
            self.assertEqual(result['requests'][0]['records'],[])
        planned['action']='create'
        result=run('semanticCalendar.prepareIntake(data.planned,data.existing)',{'planned':planned,'existing':[event('a')]})
        self.assertEqual([r['id'] for r in result['requests'][0]['records']],['incoming','e0'])

    def test_upsert_review_contract_and_exact_guard_worker_unchanged(self):
        patch=operations('intake',intake_workflow(),'test-credential')
        self.assertFalse(any(op.get('nodeName')=='Merge existing calendar event' for op in patch))
        self.assertFalse(any(op.get('nodeName')=='Recheck missing details on writable copy' for op in patch))
        confirm=next(op['parameters']['jsCode'] for op in patch if op.get('nodeName')=='Confirm calendar intake')
        self.assertIn("if(planned.action==='review')",confirm)
        self.assertIn('ok:false,review_required:true',confirm)
        request=next(op['node'] for op in patch if op.get('node',{}).get('name')=='Find semantic calendar candidates')
        self.assertFalse(request['parameters']['options']['redirect']['redirect']['followRedirects'])
        self.assertEqual(request['parameters']['options']['timeout'],12000)
        self.assertEqual(request['parameters']['options']['batching']['batch'],{'batchSize':2,'batchInterval':250})

    def test_actual_intake_hook_withholds_ambiguous_create_and_confirm_reports_review(self):
        patch=operations('intake',intake_workflow(),'test-credential')
        apply=next(op['node']['parameters']['jsCode'] for op in patch if op.get('node',{}).get('name')=='Apply semantic calendar review guard')
        confirm=next(op['parameters']['jsCode'] for op in patch if op.get('nodeName')=='Confirm calendar intake')
        planned={'action':'create','actionIndex':0,'person':'household_one','eventId':'new','summary':'Class dinner',
          'start':'2026-10-26T18:00:00+01:00','end':'2026-10-26T20:00:00+01:00','allDay':False}
        contexts={'Merge existing calendar event':[planned],'Find same-day calendar events':[event('existing')]}
        response={'available':True,'pairs':[{'left_id':'incoming','right_id':'e0','score':0.99}]}
        result=run_node(apply,contexts,[response])[0]['json']
        self.assertEqual(result['action'],'review')
        self.assertEqual(result['actionIndex'],2)
        self.assertNotIn('request',result)
        final=run_node(confirm,{'Apply semantic calendar review guard':[result]},[result])[0]['json']
        self.assertFalse(final['ok'])
        self.assertTrue(final['review_required'])
        for action,index in [('noop',2),('update',1)]:
            contexts['Merge existing calendar event']=[{**planned,'action':action,'actionIndex':index}]
            unchanged=run_node(apply,contexts,[response])[0]['json']
            self.assertEqual(unchanged,contexts['Merge existing calendar event'][0])
        contexts['Merge existing calendar event']=[planned]
        unchanged=run_node(apply,contexts,[{'error':'timeout'}])[0]['json']
        self.assertEqual(unchanged,planned)

    def test_actual_audit_hook_adds_quiet_review_preserving_exact_plans_and_work_boundaries(self):
        readers=[{'name':'Read audit '+name+' calendar','entity':name} for name in ['one','two','three','four','five','work']]
        workflow={'nodes':[{'name':'Plan conservative calendar merges','position':[0,0],
          'parameters':{'jsCode':'const readers='+json.dumps(readers)+'; EXACT PLANNER'}},
          {'name':'Build quiet audit report','parameters':{'jsCode':"const audit=$('Plan conservative calendar merges').first().json;const labels={different_calendars:'exact'};"}}]}
        patch=operations('audit',workflow,'test-credential')
        apply=next(op['node']['parameters']['jsCode'] for op in patch if op.get('node',{}).get('name')=='Apply semantic calendar review guard')
        self.assertNotIn('Read audit work calendar',apply)
        self.assertFalse(any(op.get('nodeName')=='Plan conservative calendar merges' for op in patch))
        audit={'context':{'startDate':'2026-10-26','endDate':'2026-12-25','dry_run':False},'plans':[],'reviews':[],'checked':3}
        contexts={'Plan conservative calendar merges':[audit],**{reader['name']:[] for reader in readers}}
        contexts['Read audit one calendar']=[event('a'),event('b','Class dinner')]
        contexts['Read audit work calendar']=[event('PRIVATE WORK')]
        response={'available':True,'pairs':[{'left_id':'r0','right_id':'r1','score':0.99}]}
        result=run_node(apply,contexts,[response])[0]['json']
        self.assertEqual(result['plans'],audit['plans'])
        self.assertEqual(result['reviews'][0]['ids'],['a','b'])
        self.assertEqual(result['context'],audit['context'])
        for response in [{'available':False},{'error':'timeout'}]:
            self.assertEqual(run_node(apply,contexts,[response])[0]['json'],audit)


if __name__=='__main__': unittest.main()
