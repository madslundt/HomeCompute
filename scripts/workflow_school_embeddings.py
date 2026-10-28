"""Patch scheduled Aula evidence archiving without persisting live workflow data."""
from __future__ import annotations

from pathlib import Path
from typing import Any

DIRECTORY=Path(__file__).resolve().parents[1]/'automations/semantic-school'
ENDPOINT='https://ai.home.arpa/internal/workflow-retrieval'


def operations(workflow: dict[str,Any], credential_id: str) -> list[dict[str,Any]]:
    nodes={node['name']:node for node in workflow['nodes']}
    shared=(DIRECTORY/'evidence.js').read_text()
    edges={(name,edge['node']) for name,ports in workflow.get('connections',{}).items() for output in ports.get('main',[]) for edge in output}
    result=[]
    for stage,source,target in [('prefetched','Attach fetched Aula sources','Aula AI Agent'),('expanded','Evaluate Aula result','Retry?')]:
        names={key:title+' '+stage+' Aula archive' for key,title in [('prepare','Prepare'),('archive','Store'),('restore','Restore context after')]}
        if stage=='prefetched':
            code="""
const context=$('Attach fetched Aula sources').first(0,$runIndex).json;
const sources=[];
for(const [name,tool] of [['Aula Calendar Source','aula_calendar_events'],['Aula Next Week Calendar Source','aula_calendar_events'],
 ['Aula Messages Source','aula_messages_list_threads'],['Aula Posts Source','aula_posts_list'],['Aula Weekly Letter Source','aula_ugebrev_minuddannelse']]) {
  try{sources.push({tool,output:$(name).first(0,$runIndex).json});}catch(_){}
}
return schoolEvidence.requests(schoolEvidence.fromRaw(sources,context)).map(request=>({json:{request}}));
"""
        else:
            code="""
const context=$('Evaluate Aula result').first(0,$runIndex).json;
const rows=['ok','partial'].includes(context.collectorStatus)&&context.shouldRetry!==true?schoolEvidence.fromEvidence(context.sourceEvidence):[];
return schoolEvidence.requests(rows).map(request=>({json:{request}}));
"""
        position=nodes[source].get('position',[0,0])
        added=[
          {'name':names['prepare'],'type':'n8n-nodes-base.code','typeVersion':2,'position':[position[0]+160,position[1]+240],
           'parameters':{'mode':'runOnceForAllItems','jsCode':shared+code}},
          {'name':names['archive'],'type':'n8n-nodes-base.httpRequest','typeVersion':4.5,'position':[position[0]+360,position[1]+240],
           'parameters':{'method':'POST','url':ENDPOINT,'authentication':'genericCredentialType','genericAuthType':'httpHeaderAuth',
             'sendBody':True,'specifyBody':'json','jsonBody':'={{ JSON.stringify($json.request) }}',
             'options':{'timeout':18000,'batching':{'batch':{'batchSize':2,'batchInterval':250}},'redirect':{'redirect':{'followRedirects':False}},'response':{'response':{'neverError':True,'responseFormat':'json'}}}},
           'credentials':{'httpHeaderAuth':{'id':credential_id,'name':'HomeCompute retrieval · school'}}},
          {'name':names['restore'],'type':'n8n-nodes-base.code','typeVersion':2,'position':[position[0]+560,position[1]+240],
           'parameters':{'mode':'runOnceForAllItems','jsCode':"return $('"+source+"').all(0,$runIndex);"}},
        ]
        for node in added:
            result.append({'type':'updateNodeParameters','nodeName':node['name'],'parameters':node['parameters'],'replace':True} if node['name'] in nodes else {'type':'addNode','node':node})
        result.extend([
          {'type':'setNodeSettings','nodeName':names['archive'],'settings':{'onError':'continueRegularOutput','retryOnFail':False}},
          {'type':'setNodeSettings','nodeName':names['prepare'],'settings':{'onError':'continueRegularOutput','retryOnFail':False}},
          {'type':'setNodeCredential','nodeName':names['archive'],'credentialKey':'httpHeaderAuth','credentialId':credential_id,'credentialName':'HomeCompute retrieval · school'},
        ])
        if (source,target) in edges:result.append({'type':'removeConnection','source':source,'target':target})
        for left,right in [(source,names['prepare']),(names['prepare'],names['archive']),(names['archive'],names['restore']),(names['restore'],target)]:
            if (left,right) not in edges:result.append({'type':'addConnection','source':left,'target':right})
    # Keep the original canvas readable as archive stages are added. Existing
    # groups are left intact; the initial collector has no groups.
    if not workflow.get('nodeGroups'):
        groups=[
          ('Prepare run context',['Build structured Aula prompt','Prepare retry state','Overview — Aula Collector & Analyze'],
           'Set the current school week, collection instructions and retry deadline before reading sources.'),
          ('Collect school evidence',['Aula Discovery','Attach Aula discovery context','Aula Calendar Source','Aula Next Week Calendar Source',
            'Aula Messages Source','Aula Posts Source','Aula Weekly Letter Source','Weekly message available?','Aula Weekly Message Source',
            'Record current weekly message source','Attach fetched Aula sources','Prepare prefetched Aula archive','Store prefetched Aula archive',
            'Restore context after prefetched Aula archive'],
           'Read school sources and archive their original wording without changing the collection context.'),
          ('Analyze source evidence',['Aula AI Agent','Aula MCP','HomeCompute automation model','Evaluate Aula result',
            'Prepare expanded Aula archive','Store expanded Aula archive','Restore context after expanded Aula archive','Retry?'],
           'Inspect relevant details, verify source references and archive successfully retrieved evidence before choosing a retry.'),
          ('Check delivery quality',['Normalize structured facts','Daily source-grounded?','Review Aula draft','Local Aula summary reviewer',
            'Aula review JSON parser','Evaluate Aula review','Check Aula delivery quality','Retry incomplete Aula?'],
           'Check grounded facts and the family briefing, then return the result or retry an incomplete collection.'),
        ]
        for name,members,description in groups:
            if all(member in nodes or member.startswith(('Prepare prefetched','Store prefetched','Restore context after prefetched','Prepare expanded','Store expanded','Restore context after expanded')) for member in members):
                result.append({'type':'addNodeGroup','name':name,'nodeNames':members,'description':description})
    return result
