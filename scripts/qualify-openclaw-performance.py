#!/usr/bin/env python3
"""Run bounded isolated native candidates while the production gateway stays up.

Run as the native config owner through managed exec. Files and receipts are
private qualification data outside the production state root. No service,
installed module, production configuration or existing session is modified.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import time

from openclaw_performance import reasoning_candidate, browser_candidate, BROWSER_FILE

NATIVE = Path('/sandbox/.openclaw/openclaw.json')
RUNTIME = Path('/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist')


def write(path: Path, content: str) -> None:
    with path.open('x') as stream:
        stream.write(content)


def validate_output(path: Path) -> None:
    if (path.exists() or path.is_symlink() or path.parent!=Path('/sandbox')
            or not path.name.startswith('perf-') or path.parent.resolve()!=path.parent):
        raise ValueError('New /sandbox/perf-* directory outside production state required; no replay')


def configuration(live: dict, workspace: Path) -> dict:
    model = copy.deepcopy(next(m for m in live['models']['providers']['inference']['models'] if m['id']=='automation-moe'))
    model['maxTokens'] = 1024
    return {
        'gateway': {'mode':'local'},
        'models': {'mode':'replace','catalogRefresh':{'enabled':False},'providers':{'inference':{
            'baseUrl':'https://inference.local/v1','api':'openai-completions','apiKey':'unused','models':[model]}}},
        'agents': {'defaults': {'workspace':str(workspace),'skipBootstrap':True,
            'model':{'primary':'inference/automation-moe','fallbacks':[]},
            'thinkingDefault':'off','maxConcurrent':1,'timeoutSeconds':30,
            'skills':[],'heartbeat':{'every':'0m'},'userTimezone':'Europe/Copenhagen'},
            'entries':{'main':{'name':'Synthetic performance qualification'}}},
        'tools': {'profile':'full','allow':['read','browser','session_status'],
                  'deny':['exec','process','write','edit','apply_patch','sessions_spawn','sessions_send'],
                  'fs':{'workspaceOnly':True},'elevated':{'enabled':False},
                  'loopDetection':copy.deepcopy(live['tools'].get('loopDetection',{'enabled':True}))},
        'browser': copy.deepcopy(live['browser']),
        'plugins': {'allow':['homecompute-safety'],
            'load':{'paths':[p for p in live['plugins']['load']['paths'] if 'safety-plugin' in p]},
            'entries':{'homecompute-safety':{'enabled':True}}},
    }


def main() -> None:
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--variant',choices=['before','reasoning','schema','schema-only'],required=True)
    parser.add_argument('--case',default='budget-en')
    args=parser.parse_args()
    validate_output(args.output)
    args.output.mkdir(mode=0o700,parents=True)
    workspace=args.output/'workspace';workspace.mkdir(mode=0o700)
    state=args.output/'state';state.mkdir(mode=0o700)
    live_raw=NATIVE.read_bytes();live=json.loads(live_raw)
    fixture=json.loads((args.source/'benchmarks/assistant-comparison/cases.json').read_text())
    if args.case=='read-fixture':
        write(workspace/'fixture.json',json.dumps({'rice_dkk':30,'lentils_dkk':20}))
        case={'role':'finance','expected_json':{'total_dkk':50}}
        prompt=('Synthetic read-only tool qualification. First use read to load '+str(workspace/'fixture.json')+
                '. Calculate the total from that file. Return only JSON with total_dkk. '
                'Use no other tool, service, memory, delegation, write or delivery.')
    else:
        case=next(c for c in fixture['cases'] if c['id']==args.case)
        if case.get('exclude_from_score'):raise ValueError('Excluded ambiguous fixture refused')
        prompt=fixture['instructions']+'\n\n'+case['prompt']
    role=live['agents']['entries'][case['role']]
    # Copy this role's policy file only, never its memory, sessions or auth.
    write(workspace/'AGENTS.md',(Path(role['workspace'])/'AGENTS.md').read_text())
    write(args.output/'prompt.txt',prompt)
    config=configuration(live,workspace)
    if args.variant in {'reasoning','schema'}: config=reasoning_candidate(config)
    env=dict(os.environ)
    if args.variant in {'schema','schema-only'}:
        helper=args.output/'browser-schema.mjs'
        write(helper,(args.source/'deploy/openclaw/safety-plugin/browser-schema.mjs').read_text())
        patched=browser_candidate((RUNTIME/BROWSER_FILE).read_bytes(),helper.as_uri())
        write(args.output/'browser-candidate.js',patched.decode())
        write(args.output/'loader.mjs',(args.source/'benchmarks/openclaw-optimization/loader.mjs').read_text())
        env['NODE_OPTIONS']=(env.get('NODE_OPTIONS','')+' --import '+str(args.output/'loader.mjs')).strip()
    write(args.output/'config.json',json.dumps(config,indent=2)+'\n')
    command=['openclaw','agent','exec','--config',str(args.output/'config.json'),
             '--state-dir',str(state),'--cwd',str(workspace),'--thinking','off','--timeout','30',
             '--message-file',str(args.output/'prompt.txt'),'--json']
    started=time.monotonic()
    try:
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
    except subprocess.TimeoutExpired as failure:
        for name,data in [('stdout.json',failure.stdout),('stderr.txt',failure.stderr)]:
            write(args.output/name,data.decode(errors='replace') if isinstance(data,bytes) else data or '')
        write(args.output/'uncertain.json',json.dumps({'uncertain':True,'no_replay':True}))
        raise SystemExit('Qualification outcome uncertain; reconcile before another run') from None
    write(args.output/'stdout.json',result.stdout)
    write(args.output/'stderr.txt',result.stderr)
    try:value=json.loads(result.stdout)
    except ValueError:value={}
    if not isinstance(value,dict):value={}
    answer=value.get('final','')
    if not isinstance(answer,str):answer=''
    try:
        parsed=json.loads(answer);strict=True
    except ValueError:
        strict=False
        try:parsed=json.loads(answer.strip().removeprefix('```json').removesuffix('```').strip())
        except ValueError:parsed={}
    if not isinstance(parsed,dict): parsed={}
    checks={k:k in parsed and type(parsed[k]) is type(expected) and parsed[k]==expected
            for k,expected in case['expected_json'].items()}
    for key,items in case.get('required_items',{}).items():
        checks[key]=isinstance(parsed.get(key),list) and all(item in parsed[key] for item in items)
    tools=[];failures=[];transcript_events=0
    for database in state.rglob('openclaw-agent.sqlite'):
        db=sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True)
        events=db.execute('SELECT event_json FROM transcript_events WHERE session_id=? ORDER BY seq LIMIT 1000',
                          (value.get('sessionId'),)).fetchall();db.close()
        transcript_events+=len(events)
        for row in events:
            message=json.loads(row[0]).get('message',{})
            if message.get('role')=='assistant':
                for part in message.get('content',[]):
                    if not isinstance(part,dict):continue
                    if part.get('type')=='toolCall': tools.append(part.get('name'))
            if message.get('role')=='toolResult' and message.get('isError'):failures.append(message.get('toolName'))
    if args.case=='read-fixture':checks['fixture_read']=tools==['read'] and not failures
    else:checks['no_tools']=not tools
    checks['completed']=value.get('ok') is True and value.get('status')=='ok'
    checks['local_route']=value.get('model')=='automation-moe' and value.get('provider')=='inference'
    checks['transcript_observed']=transcript_events>0
    record={'variant':args.variant,'case':args.case,'role':case['role'],'exit':result.returncode,
            'elapsed_seconds':round(time.monotonic()-started,3),
            'production_config_sha256':hashlib.sha256(live_raw).hexdigest(),
            'production_config_unchanged':NATIVE.read_bytes()==live_raw,
            'usage':value.get('usage'),'model':value.get('model'),'provider':value.get('provider'),
            'status':value.get('status'),'semantic_checks':checks,'semantic_pass':all(checks.values()),
            'strict_json':strict,'tool_calls':tools,'tool_failures':failures,
            'transcript_events':transcript_events}
    write(args.output/'receipt.json',json.dumps(record,indent=2)+'\n')
    print(json.dumps(record),flush=True)
    if result.returncode or not record['semantic_pass'] or not record['production_config_unchanged']:
        raise SystemExit(result.returncode or 1)


if __name__=='__main__': main()
