#!/usr/bin/env python3
"""One synthetic Atlas→Nova→Atlas handoff; private evidence, no channel delivery."""
import argparse
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid


def verify(output, language):
    os.umask(0o077)
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Private output required')
    output.mkdir(mode=0o700)
    session=str(uuid.uuid4())
    instruction=("This is an authorized synthetic read-only qualification. Load task-ownership. "
        "Delegate exactly one isolated run to researcher (Nova), mode run, timeout 300. "
        "Give Nova only this supplied evidence: fictional sample A has 13 points and sample B has 29 points. "
        "Ask Nova for their sum with the two supplied source labels. No web search, service calls, memory writes, "
        "coding, saved tasks, reminders or messages. Yield once with a short acknowledgment, wait for Nova, "
        "then give the reviewed result in this CLI session. There is no Mattermost delivery destination. "
        "Reply in "+('Danish.' if language=='da' else 'English.'))
    prompt=output/'prompt.txt';prompt.write_text(instruction);prompt.chmod(0o600)
    process=subprocess.run(['openclaw','agent','--agent','main','--session-id',session,'--message-file',str(prompt),
                            '--timeout','180','--json'],capture_output=True,text=True,timeout=210)
    for name,data in [('native-result.json',process.stdout),('native-stderr.txt',process.stderr)]:
        p=output/name;p.write_text(data);p.chmod(0o600)
    path=Path('/sandbox/.openclaw/agents/main/agent/openclaw-agent.sqlite')
    deadline=time.monotonic()+180;calls=[];answers=[];events=[];failures=[];results=[]
    child_verified=False;child_tool_names=[]
    while True:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
            events=[json.loads(row[0]) for row in db.execute('SELECT event_json FROM transcript_events WHERE session_id=? ORDER BY seq LIMIT 1000',(session,))]
        calls=[];answers=[];failures=[];results=[]
        for event in events:
            message=event.get('message',{});body=message.get('content',[])
            if not isinstance(body,list):continue
            if message.get('role')=='assistant':
                calls += [x for x in body if x.get('type')=='toolCall']
                answers += [x.get('text','') for x in body if x.get('type')=='text']
            if message.get('role')=='toolResult':
                results.append(message)
                if message.get('isError'):failures.append(message.get('toolName'))
        spawns=[x for x in calls if x.get('name')=='sessions_spawn']
        yields=[x for x in calls if x.get('name')=='sessions_yield']
        answer='\n'.join(answers)
        child_keys=set(re.findall(r'agent:researcher:subagent:[0-9a-f-]{36}',json.dumps(results)))
        child_verified=False;child_tool_names=[]
        if len(child_keys)==1:
            child_path=Path('/sandbox/.openclaw/agents/researcher/agent/openclaw-agent.sqlite')
            if child_path.exists():
                with sqlite3.connect('file:'+str(child_path)+'?mode=ro',uri=True) as db:
                    child=db.execute('SELECT current_session_id FROM session_nodes WHERE session_key=?',(next(iter(child_keys)),)).fetchone()
                    child_events=[json.loads(row[0]) for row in db.execute('SELECT event_json FROM transcript_events WHERE session_id=? ORDER BY seq LIMIT 1000',(child[0],))] if child else []
                child_answers=[]
                for event in child_events:
                    message=event.get('message',{});body=message.get('content',[])
                    if message.get('role')!='assistant' or not isinstance(body,list):continue
                    child_answers += [x.get('text','') for x in body if x.get('type')=='text']
                    child_tool_names += [x.get('name') for x in body if x.get('type')=='toolCall']
                child_verified='42' in '\n'.join(child_answers) and all(n in {'read','session_status'} for n in child_tool_names)
        if ('42' in answer and spawns and yields and child_verified) or time.monotonic()>=deadline:break
        time.sleep(2)
    safe_names={'read','agents_list','sessions_spawn','sessions_yield','subagents','session_status'}
    args=spawns[0].get('arguments',{}) if len(spawns)==1 else {}
    passed=(process.returncode==0 and len(spawns)==1 and len(yields)==1 and args.get('agentId')=='researcher'
        and args.get('mode')=='run' and args.get('context')=='isolated' and args.get('runTimeoutSeconds')==300
        and child_verified and bool(yields[0].get('arguments',{}).get('acknowledgment')) and '42' in answer and not failures
        and all(x.get('name') in safe_names for x in calls))
    receipt={'session_id':session,'language':language,'native_exit':process.returncode,
        'parent_tool_names':[x.get('name') for x in calls],'spawn_count':len(spawns),'yield_count':len(yields),
        'tool_failures':failures,'child_result_verified':child_verified,'child_tool_names':child_tool_names,'answer_contains_supplied_sum':'42' in answer,'passed':bool(passed),
        'synthetic_only':True,'channel_delivery_requested':False}
    p=output/'receipt.json';p.write_text(json.dumps(receipt,indent=2)+'\n');p.chmod(0o600)
    p=output/'parent-events.json';p.write_text(json.dumps(events));p.chmod(0o600)
    print(json.dumps(receipt));return passed


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--language',choices=('en','da'),default='en')
    a=p.parse_args();raise SystemExit(0 if verify(a.output,a.language) else 1)
