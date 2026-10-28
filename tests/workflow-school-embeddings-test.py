"""Source-only scheduled Aula archiving and retry-context fixtures."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from workflow_school_embeddings import operations
SHARED=(ROOT/'automations/semantic-school/evidence.js').read_text()


def run(expression,data):
    code="const fs=require('fs'),data=JSON.parse(fs.readFileSync(0,'utf8'));\n"+SHARED+'\nprocess.stdout.write(JSON.stringify('+expression+'));'
    return json.loads(subprocess.run(['node','-e',code],input=json.dumps(data),capture_output=True,text=True,check=True).stdout)


def observation(value,**changes):
    return {'content':[{'type':'text','text':json.dumps(value)}],**changes}


def workflow():
    names=['Attach fetched Aula sources','Aula AI Agent','Evaluate Aula result','Retry?']
    return {'nodes':[{'name':name,'parameters':{},'position':[index*100,0]} for index,name in enumerate(names)],
      'connections':{names[0]:{'main':[[{'node':names[1]}]]},names[2]:{'main':[[{'node':names[3]}]]}}}


class SchoolEmbeddingTests(unittest.TestCase):
    def test_prefetched_raw_sources_stable_ids_exclude_prompts_and_generated_text(self):
        sources=[{'tool':'aula_messages_list_threads','output':observation({'threads':[{'id':7,'subject':'Trip',
          'latestMessage':{'sendDateTime':'2026-10-26T10:00:00Z','text':{'html':'Bring <b>boots</b> &amp; a jacket'}}}]})},
          {'tool':'aula_posts_list','output':observation({'posts':[{'id':7,'title':'School','content':'Entrance closes at four'}]})}]
        result=run('schoolEvidence.fromRaw(data.sources,data.context)',{'sources':sources,'context':{'userPrompt':'GENERATED PROMPT'}})
        self.assertEqual({r['id'] for r in result},{'aula:message:7','aula:post:7'})
        self.assertIn('Bring boots & a jacket',result[0]['text'])
        self.assertNotIn('GENERATED',json.dumps(result))
        self.assertEqual(result[0]['metadata']['source_record_id'],'7')

    def test_failures_invalid_ids_discovery_and_title_only_rows_are_not_evidence(self):
        cases=[{'tool':'aula_posts_list','output':observation({'posts':[{'title':'no id','content':'body'}]})},
          {'tool':'aula_posts_list','output':observation({'posts':[{'id':1,'title':'title only'}]})},
          {'tool':'aula_posts_list','output':observation({'posts':[{'id':1,'content':'body'}]},isError=True)},
          {'tool':'aula_posts_list','output':observation({'error':'unavailable','posts':[{'id':1,'content':'body'}]})},
          {'tool':'aula_discover','output':observation({'items':[{'id':1,'content':'discovery'}]})},
          {'tool':'aula_posts_list','output':{'content':[{'type':'text','text':'invalid JSON'}]}}]
        for source in cases:
            self.assertEqual(run('schoolEvidence.fromRaw([data],{})',source),[])

    def test_weekly_letter_matches_existing_verified_source_id_and_requires_child_date_week(self):
        data={'sources':[{'tool':'aula_ugebrev_minuddannelse','output':observation({'items':[{'childName':'Synthetic Child','date':'2026-10-26','content':'Full weekly letter'}]})}],
          'context':{'isoWeek':'2026-W44','aulaDiscovery':{'children':[{'id':4,'name':'Synthetic Child'}]}}}
        result=run('schoolEvidence.fromRaw(data.sources,data.context)',data)
        self.assertEqual(result[0]['metadata']['source_record_id'],'aula_ugebrev_minuddannelse:4:2026-W44:2026-10-26')
        data['context']['aulaDiscovery']['children']=[]
        self.assertEqual(run('schoolEvidence.fromRaw(data.sources,data.context)',data),[])

    def test_raw_calendar_occurrence_retains_title_and_source_times(self):
        source={'tool':'aula_calendar_events','output':observation({'events':[{'id':19,'title':'School social evening',
          'start':'2026-10-26T18:00:00+01:00','end':'2026-10-26T20:00:00+01:00'}]})}
        record=run('schoolEvidence.fromRaw([data],{})',source)[0]
        self.assertEqual(record['id'],'aula:calendar:19')
        self.assertIn('School social evening',record['text'])
        self.assertIn('2026-10-26T18:00:00+01:00',record['text'])

    def test_expanded_evidence_requires_found_raw_text_and_omits_generated_claim(self):
        rows=[{'found':True,'tool':'aula_messages_get_thread','source_id':'7','source_title':'Trip','source_text':'Actual source',
          'source_date':'2026-10-26','claim':'GENERATED CLAIM'},
          {'found':False,'tool':'aula_posts_list','source_id':'9','source_text':'not found'},
          {'found':True,'tool':'aula_discover','source_id':'8','source_text':'discovery'}]
        result=run('schoolEvidence.fromEvidence(data)',json.dumps(rows))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['id'],'aula:message:7')
        self.assertNotIn('GENERATED',json.dumps(result))

    def test_unicode_record_budget_and_prefixed_tool_aliases(self):
        result=run("schoolEvidence.record('aula_messages_get_thread','aula_messages_list_threads:7',data)",'🧦æ'*10000)
        self.assertLessEqual(len(result['text'].encode()),16000)
        self.assertEqual(result['id'],'aula:message:7')
        self.assertEqual(result['metadata']['source_record_id'],'7')

    def test_batches_bound_bytes_and_rows_and_dedupe_aliases(self):
        rows=[{'id':str(i),'text':'æ'*16000,'metadata':{}} for i in range(130)]
        result=run('schoolEvidence.requests(data)',rows)
        self.assertEqual(sum(len(request['records']) for request in result),130)
        for request in result:
            self.assertLessEqual(len(request['records']),128)
            self.assertLess(len(json.dumps(request,ensure_ascii=False).encode()),125000)
        result=run('schoolEvidence.requests(data)',[{'id':'same','text':'short'},{'id':'same','text':'fuller source content'}])
        self.assertEqual(len(result[0]['records']),1)
        self.assertEqual(result[0]['records'][0]['text'],'fuller source content')

    def test_atomic_hook_preserves_existing_nodes_subnodes_and_retry_run_pairing(self):
        ops=operations(workflow(),'school-profile')
        self.assertFalse(any(op.get('nodeName') in ['Attach fetched Aula sources','Aula AI Agent','Evaluate Aula result'] for op in ops))
        added=[op['node'] for op in ops if op['type']=='addNode']
        self.assertEqual(len(added),6)
        for node in added:
            if node['type'].endswith('.httpRequest'):
                self.assertEqual(node['parameters']['url'],'https://ai.home.arpa/internal/workflow-retrieval')
                self.assertEqual(node['parameters']['options']['timeout'],18000)
                self.assertEqual(node['parameters']['options']['batching']['batch'],{'batchSize':2,'batchInterval':250})
                self.assertFalse(node['parameters']['options']['redirect']['redirect']['followRedirects'])
            if node['name'].startswith('Restore context'):
                self.assertIn('.all(0,$runIndex)',node['parameters']['jsCode'])
        restore=next(node['parameters']['jsCode'] for node in added if node['name']=='Restore context after prefetched Aula archive')
        harness="""const fs=require('fs'),data=JSON.parse(fs.readFileSync(0,'utf8'));const runs=[[{json:{retry:0}}],[{json:{retry:1,userPrompt:'current'}}]];
const lookup=()=>({all:(branch,run)=>runs[run]});
process.stdout.write(JSON.stringify(new Function('$','$runIndex',data.source)(lookup,1)));"""
        result=json.loads(subprocess.run(['node','-e',harness],input=json.dumps({'source':restore}),capture_output=True,text=True,check=True).stdout)
        self.assertEqual(result,[{'json':{'retry':1,'userPrompt':'current'}}])


if __name__=='__main__':unittest.main()
