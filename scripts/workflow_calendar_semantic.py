"""Build minimal, inspectable patches for live calendar workflows without exports.

Call operations(kind, live_workflow, credential_id) after retrieving fresh private
workflow data. No calendar IDs, emails, execution data or credentials are persisted.
The existing exact matching, merge worker, freshness checks and writes stay intact.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

DIRECTORY = Path(__file__).resolve().parents[1] / 'automations/semantic-calendar'
ENDPOINT = 'https://ai.home.arpa/internal/workflow-retrieval'


def operations(kind: str, workflow: dict[str, Any], credential_id: str) -> list[dict[str, Any]]:
    if kind not in {'intake', 'audit'}:
        raise ValueError('Unknown calendar workflow')
    nodes = {node['name']: node for node in workflow['nodes']}
    shared = (DIRECTORY / 'candidates.js').read_text()
    prepare_name = 'Prepare semantic calendar candidates'
    retrieve_name = 'Find semantic calendar candidates'
    apply_name = 'Apply semantic calendar review guard'
    source = 'Merge existing calendar event' if kind == 'intake' else 'Plan conservative calendar merges'
    target = 'Choose calendar write' if kind == 'intake' else 'Duplicates need processing?'
    if kind == 'intake':
        prepare = """
const planned=$('Merge existing calendar event').first().json;
const existing=$('Find same-day calendar events').all().map(item=>item.json);
const prepared=semanticCalendar.prepareIntake(planned,existing);
return prepared.requests.map(request=>({json:{request}}));
"""
        apply = """
const planned=$('Merge existing calendar event').first().json;
const existing=$('Find same-day calendar events').all().map(item=>item.json);
const prepared=semanticCalendar.prepareIntake(planned,existing);
const pairs=semanticCalendar.candidates(prepared,$input.all().map(item=>item.json)).filter(pair=>pair.left.id==='incoming'||pair.right.id==='incoming');
if(planned.action!=='create'||!pairs.length)return [{json:planned}];
const ids=[...new Set(pairs.map(pair=>pair.left.id==='incoming'?pair.right.source_id:pair.left.source_id))].sort();
return [{json:{...planned,action:'review',actionIndex:2,review_required:true,
  review_items:[{reason:'semantic_candidate',entities:[planned.person],ids:[planned.eventId,...ids].sort(),date:semanticCalendar.day(planned.start),title:planned.summary}],
  reason:'possible_duplicate_requires_review'}}];
"""
    else:
        original = nodes[source]['parameters']['jsCode']
        readers_match = re.search(r'const readers=(\[[^;]+\]);', original)
        if not readers_match:
            raise ValueError('Calendar audit reader hook changed; review before updating')
        readers = json.loads(readers_match.group(1))
        # Work admission is intentionally excluded from all new embedding requests.
        readers = [row for row in readers if 'work' not in row['name'].lower()]
        if len(readers) != 5:
            raise ValueError('Unexpected household reader set; review data boundaries')
        collect = '\nconst readers=' + json.dumps(readers, separators=(',', ':')) + ";\n" + """
const audit=$('Plan conservative calendar merges').first().json;
const rows=readers.flatMap(source=>$(source.name).all().map(item=>({entity:source.entity,event:item.json})));
const prepared=semanticCalendar.prepare(rows,audit.context);
"""
        prepare = collect + "return prepared.requests.map(request=>({json:{request}}));\n"
        apply = collect + "return [{json:semanticCalendar.appendReviews(audit,semanticCalendar.candidates(prepared,$input.all().map(item=>item.json)))}];\n"
    position = nodes[source].get('position', [0, 0])
    additions = [
        {'name': prepare_name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2,
         'position': [position[0]+160, position[1]+240], 'parameters': {'mode':'runOnceForAllItems','jsCode':shared+prepare}},
        {'name': retrieve_name, 'type':'n8n-nodes-base.httpRequest','typeVersion':4.5,
         'position':[position[0]+360,position[1]+240],
         'parameters':{'method':'POST','url':ENDPOINT,'authentication':'genericCredentialType','genericAuthType':'httpHeaderAuth',
                       'sendBody':True,'specifyBody':'json','jsonBody':'={{ JSON.stringify($json.request) }}',
                       'options':{'timeout':12000,'batching':{'batch':{'batchSize':2,'batchInterval':250}},'redirect':{'redirect':{'followRedirects':False}},
                                  'response':{'response':{'neverError':True,'responseFormat':'json'}}}},
         'credentials':{'httpHeaderAuth':{'id':credential_id,'name':'HomeCompute retrieval · calendar'}}},
        {'name':apply_name,'type':'n8n-nodes-base.code','typeVersion':2,
         'position':[position[0]+560,position[1]+240],'parameters':{'mode':'runOnceForAllItems','jsCode':shared+apply}},
    ]
    result=[]
    for node in additions:
        if node['name'] in nodes:
            result.append({'type':'updateNodeParameters','nodeName':node['name'],'parameters':node['parameters'],'replace':True})
        else:
            result.append({'type':'addNode','node':node})
    result.extend([
        {'type':'setNodeSettings','nodeName':retrieve_name,'settings':{'onError':'continueRegularOutput','retryOnFail':False}},
        {'type':'setNodeCredential','nodeName':retrieve_name,'credentialKey':'httpHeaderAuth','credentialId':credential_id,'credentialName':'HomeCompute retrieval · calendar'},
    ])
    # Every mutation is in one atomic batch; never remove/recreate existing nodes.
    edges=[(source,prepare_name),(prepare_name,retrieve_name),(retrieve_name,apply_name),(apply_name,target)]
    connections=workflow.get('connections',{})
    existing_edges={(name,edge['node']) for name,ports in connections.items() for output in ports.get('main',[]) for edge in output}
    if (source,target) in existing_edges:
        result.append({'type':'removeConnection','source':source,'target':target})
    for left,right in edges:
        if (left,right) not in existing_edges: result.append({'type':'addConnection','source':left,'target':right})
    if kind == 'intake':
        confirm=nodes['Confirm calendar intake']['parameters']['jsCode']
        confirm=confirm.replace("$('Merge existing calendar event').first().json", "$('Apply semantic calendar review guard').first().json")
        marker='const saved=$input.first().json;'
        guard="if(planned.action==='review')return [{json:{...planned,ok:false,review_required:true}}];"
        if guard not in confirm:
            if marker not in confirm: raise ValueError('Calendar confirmation hook changed')
            confirm=confirm.replace(marker,marker+'\n'+guard)
        result.append({'type':'updateNodeParameters','nodeName':'Confirm calendar intake','parameters':{**nodes['Confirm calendar intake']['parameters'],'jsCode':confirm},'replace':True})
    else:
        report=nodes['Build quiet audit report']['parameters']['jsCode']
        report=report.replace("$('Plan conservative calendar merges').first().json", "$('Apply semantic calendar review guard').first().json")
        report=report.replace("const labels={different_calendars:","const labels={semantic_candidate:'mulig dublet fundet ved betydningssøgning — kræver kontrol',different_calendars:") if "semantic_candidate:" not in report else report
        result.append({'type':'updateNodeParameters','nodeName':'Build quiet audit report','parameters':{**nodes['Build quiet audit report']['parameters'],'jsCode':report},'replace':True})
    if not any(group['name']=='Semantic duplicate review' for group in workflow.get('nodeGroups',[])):
        result.append({'type':'addNodeGroup','name':'Semantic duplicate review','nodeNames':[prepare_name,retrieve_name,apply_name],
                       'description':'Find title-only duplicate candidates; semantic matches can request review and cannot authorize calendar writes.'})
    return result
