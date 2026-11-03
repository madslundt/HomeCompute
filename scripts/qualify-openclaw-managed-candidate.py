#!/usr/bin/env python3
"""Bounded offline managed-image qualification; never upgrades production."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'config/openclaw-managed-candidate.json'
PROBE = r'''
const fs = require('node:fs');
const cp = require('node:child_process');
const crypto = require('node:crypto');
const base = '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw';
const pkg = JSON.parse(fs.readFileSync(base + '/package.json', 'utf8'));
const output = {node:process.version, uid:process.getuid(), core:pkg.version, schemas:pkg.openclaw?.schemaVersions};
const expectedModules = __RUNTIME_MODULES__;
output.runtime_modules = {};
for (const [name, expected] of Object.entries(expectedModules)) {
  const path = base + '/' + expected.path;
  const hash = fs.existsSync(path) ? crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex') : null;
  output.runtime_modules[name] = {sha256:hash,matches_published_npm:hash === expected.sha256};
}
const env = {PATH:process.env.PATH, HOME:'/sandbox', OPENCLAW_STATE_DIR:'/sandbox/state', NO_COLOR:'1', CI:'1',
  NODE_OPTIONS:'--max-old-space-size=768'};
const version = cp.spawnSync('/usr/local/bin/openclaw', ['--version'], {encoding:'utf8',timeout:30000,env,maxBuffer:1048576});
output.cli_version_ok = version.status === 0 && version.stdout.includes(pkg.version);
fs.mkdirSync('/sandbox/state', {recursive:true});
const config = {gateway:{mode:'local',bind:'loopback',auth:{mode:'token',token:'synthetic-qualification-gateway'}}, agents:{defaults:{model:{primary:'fixture/model',fallbacks:[]},
  heartbeat:{every:'0m'},maxConcurrent:1},entries:{main:{name:'Synthetic qualification'}}},
  models:{providers:{fixture:{baseUrl:'http://127.0.0.1:1/v1',apiKey:'synthetic',api:'openai-completions',
    models:[{id:'model',name:'Synthetic model',contextWindow:32768,maxTokens:128}]}}},
  plugins:{enabled:false},cron:{enabled:false},hooks:{enabled:false},browser:{enabled:false}};
fs.writeFileSync('/sandbox/config.json', JSON.stringify(config), {mode:0o600});
const validation = cp.spawnSync('/usr/local/bin/openclaw', ['config','set','--batch-json','[]','--dry-run'],
  {encoding:'utf8',timeout:30000,env:{...env,OPENCLAW_CONFIG_PATH:'/sandbox/config.json'},maxBuffer:1048576});
output.synthetic_config_valid = validation.status === 0;
if (validation.status !== 0) process.stderr.write(JSON.stringify({phase:'synthetic-config-validation',
  stdout:(validation.stdout || '').slice(0,32768),stderr:(validation.stderr || '').slice(0,32768)}) + '\n');
const childFixture = `import {n as build} from ${JSON.stringify(base + '/' + expectedModules.child_result.path)};
const tail = ' CANDIDATE_FINAL_EVIDENCE=present';
const text = 'Synthetic evidence. '.repeat(100).slice(0,1800-tail.length) + tail;
const child = (i=0) => ({runId:'synthetic-'+i,childSessionKey:'agent:operator:subagent:synthetic-'+i,
  createdAt:i,label:'Synthetic evidence',task:'Synthetic read-only task',
  execution:{endedAt:100+i,outcome:{status:'ok'}},completion:{resultText:text}});
console.log(JSON.stringify({single_child_retains_evidence:build([child()]).includes(tail.trim()),
  aggregate_bound:build(Array.from({length:9},(_,i)=>child(i))).length<=4096}));`;
const childCheck = cp.spawnSync('/usr/local/bin/node',['--input-type=module','-'],
  {input:childFixture,encoding:'utf8',timeout:30000,env,maxBuffer:1048576});
output.native_child_fixture = childCheck.status === 0 ? JSON.parse(childCheck.stdout.trim().split('\n').at(-1)) : {executed:false};
if (childCheck.status !== 0) process.stderr.write(JSON.stringify({phase:'native-child-fixture',
  stderr:(childCheck.stderr || '').slice(0,32768)}) + '\n');
output.patch_modules = {};
for (const name of ['subagent-announce-output-CgCr3vGC.js','embedded-agent-DaSvA-Yk.js']) {
  const path = base + '/dist/' + name;
  output.patch_modules[name] = fs.existsSync(path) ? {present:true,sha256:crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex')} : {present:false};
}
console.log(JSON.stringify(output));
'''


def policy() -> dict:
    data = json.loads((ROOT / POLICY).read_bytes())
    if (data.get('schema_version') != 1 or data.get('production_qualified') is not False
            or data.get('platform') != 'linux/amd64'
            or not re.fullmatch(r'ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:[0-9a-f]{64}',data.get('image',''))
            or not 512 <= data.get('memory_mib',0) <= 2048
            or data.get('cpus') != 1 or data.get('pids_limit') != 128
            or not 30 <= data.get('probe_timeout_seconds',0) <= 120):
        raise ValueError('finite unqualified candidate policy required')
    return data


def cleanup(call, name: str) -> None:
    result=call(['docker','ps','-a','--filter','label=org.homecompute.candidate='+name,'--format','{{.ID}}'])
    if result.returncode:raise RuntimeError('candidate cleanup inspection unavailable')
    ids=result.stdout.splitlines()
    if not ids:return
    if len(ids)!=1 or not re.fullmatch('[0-9a-f]{12,64}',ids[0]):raise RuntimeError('candidate cleanup ownership ambiguous')
    result=call(['docker','inspect',ids[0]])
    if result.returncode:raise RuntimeError('candidate cleanup identity unavailable')
    item=json.loads(result.stdout)[0]
    if item['Name']!='/'+name or item['Config'].get('Labels',{}).get('org.homecompute.candidate')!=name:
        raise RuntimeError('candidate cleanup identity mismatch')
    result=call(['docker','rm','--force',item['Id']])
    if result.returncode:raise RuntimeError('candidate owned cleanup failed')


def bundle() -> str:
    digest = hashlib.sha256()
    for name in ('scripts/qualify-openclaw-managed-candidate.py', POLICY):
        path = ROOT / name
        if path.is_symlink(): raise ValueError('regular candidate source required')
        digest.update(name.encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def command(data: dict, name: str) -> list[str]:
    return ['docker','run','--rm','-i','--name',name,'--label','org.homecompute.candidate='+name,
            '--platform',data['platform'],'--network','none','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--memory',str(data['memory_mib'])+'m',
            '--memory-swap',str(data['memory_mib'])+'m','--cpus','1','--pids-limit','128',
            '--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
            '--tmpfs','/sandbox:rw,nosuid,size=64m,mode=1777',
            '--entrypoint','/usr/local/bin/node',data['image'],'-']


def published(revision: str, expected: str) -> None:
    if (len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision)
            or ROOT != Path.home()/'.local/share/homecompute/releases'/revision or bundle()!=expected):
        raise ValueError('exact published candidate release/hash required')
    for args in [('merge-base','--is-ancestor',revision,'origin/master')]:
        subprocess.run(['git','-C',str(ROOT),*args],check=True,capture_output=True,timeout=15)
    for name in ('scripts/qualify-openclaw-managed-candidate.py',POLICY):
        result=subprocess.run(['git','-C',str(ROOT),'show',revision+':'+name],check=True,capture_output=True,timeout=15)
        if result.stdout!=(ROOT/name).read_bytes():raise ValueError('unpublished candidate source refused')


def admit(data: dict, available: int, free: int, pressure: str) -> None:
    match=re.search(r'^full avg10=([0-9]+(?:\.[0-9]+)?)\b',pressure,re.MULTILINE)
    if not match or not 0<=float(match[1])<=100:
        raise ValueError('candidate I/O pressure admission unavailable')
    if (available<data['minimum_available_memory_mib']*1024**2
            or free<data['minimum_free_disk_gib']*1024**3
            or float(match[1])>data['maximum_io_full_avg10_percent']):
        raise ValueError('insufficient candidate headroom; production resources unchanged')


def run(data: dict, evidence: Path, *, pull: bool) -> dict:
    if socket.gethostname().split('.')[0]!='agents' or platform.machine()!='x86_64':
        raise ValueError('existing amd64 agents guest required')
    if not evidence.is_absolute() or evidence.exists() or ROOT in evidence.parents:
        raise ValueError('new private evidence directory outside source required')
    if any(p.is_symlink() for p in (evidence,*evidence.parents)):
        raise ValueError('symlinked evidence path refused')
    available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
    admit(data,available,shutil.disk_usage('/var/lib/docker').free,Path('/proc/pressure/io').read_text())
    evidence.mkdir(mode=0o700)
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(evidence),'DOCKER_HOST':'unix:///var/run/docker.sock',
         'DOCKER_CONFIG':'/var/empty'}
    def call(args, **kwargs):
        return subprocess.run(args,capture_output=True,text=True,env=env,timeout=kwargs.pop('timeout',30),**kwargs)
    if pull:
        result=call(['docker','pull','--platform',data['platform'],data['image']],timeout=600)
        if result.returncode:raise RuntimeError('immutable candidate image pull failed')
    result=call(['docker','image','inspect',data['image']])
    if result.returncode:raise ValueError('candidate image unavailable; explicit --pull required')
    inspected=json.loads(result.stdout)[0];labels=inspected['Config'].get('Labels') or {}
    required={'org.opencontainers.image.revision':data['nemoclaw_commit'],
              'org.opencontainers.image.version':data['nemoclaw_tag'],
              'io.nvidia.nemoclaw.agent':'openclaw','io.nvidia.nemoclaw.managed-image.cohort':data['cohort'],
              'io.nvidia.nemoclaw.managed-image.platform':'linux/amd64',
              'io.nvidia.nemoclaw.managed-image.contract':'1',
              'io.nvidia.nemoclaw.managed-image.startup-profile':'1',
              'io.nvidia.nemoclaw.managed-image.capabilities':'1'}
    if inspected['Id']!=data['image_config'] or inspected['Architecture']!='amd64' or inspected['Config']['User']!='sandbox' or any(labels.get(k)!=v for k,v in required.items()):
        raise ValueError('managed image provenance mismatch')
    name='hc-oc140-'+uuid.uuid4().hex[:12]
    try:
        result=call(command(data,name),input=PROBE.replace('__RUNTIME_MODULES__',json.dumps(data['runtime_modules'])),
                    timeout=data['probe_timeout_seconds'])
    finally:
        cleanup(call,name)  # A Docker client timeout does not stop its server-side container.
    # All data in this fresh networkless container is synthetic; raw diagnostics stay private.
    for suffix,value in [('stdout',result.stdout),('stderr',result.stderr)]:
        fd=os.open(evidence/suffix,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(value)
    if result.returncode:raise RuntimeError('offline candidate probe failed; private evidence retained')
    observed=json.loads(result.stdout.strip().splitlines()[-1])
    artifact_passed=(observed['node']==data['node_version'] and observed['core']==data['openclaw_version']
                     and observed['schemas']==data['schema_versions']
                     and observed['uid']!=0 and observed['cli_version_ok'] and observed['synthetic_config_valid'])
    report={'artifact_checks_passed':artifact_passed,'production_qualified':False,'observed':observed,
            'image':data['image'],'native_lifecycle_verified':False,'integration_paths_verified':False}
    fd=os.open(evidence/'report.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(report,stream)
    return report


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',choices=('plan','probe'),default='plan')
    parser.add_argument('--revision');parser.add_argument('--expected-hash')
    parser.add_argument('--evidence',type=Path);parser.add_argument('--pull',action='store_true')
    args=parser.parse_args();data=policy()
    if args.action=='plan':
        print(json.dumps({'bundle_hash':bundle(),'candidate':data,'production_changed':False}));return
    if not args.revision or not args.expected_hash or not args.evidence:parser.error('probe requires revision, reviewed hash and new evidence directory')
    published(args.revision,args.expected_hash)
    result=run(data,args.evidence,pull=args.pull);print(json.dumps(result))
    if not result['artifact_checks_passed']:raise SystemExit(1)


if __name__=='__main__':main()
