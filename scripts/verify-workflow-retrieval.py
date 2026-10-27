#!/usr/bin/env python3
"""Read-only TLS/profile probes from n8n; ephemeral synthetic model inputs only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess

PROBE = r'''
const v=JSON.parse(require('fs').readFileSync(0,'utf8'));
const url='https://ai.home.arpa/internal/workflow-retrieval';
async function call(profile,body,token=v.tokens[profile]) {
  const r=await fetch(url,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
    body:JSON.stringify({profile,...body}),signal:AbortSignal.timeout(25000)});
  let b;try{b=await r.json()}catch{};return {status:r.status,body:b};
}
(async()=>{
  const profiles=[];
  for(const profile of Object.keys(v.tokens)) {
    const r=await call(profile,{operation:profile==='calendar'?'pairs':'rank',query:'synthetic route check',records:[]});
    profiles.push({profile,status:r.status,empty:r.body?.available===false});
  }
  const denied=await call('school',{operation:'search',query:'synthetic'},v.tokens.shopping);
  const unauthorized=await call('news',{operation:'rank',query:'synthetic',records:[]},'invalid');
  const semantic=await call('shopping',{operation:'rank',query:'waterproof boots for rain',limit:2,
    records:[{id:'boots',text:'waterproof rubber boots for rainy weather',metadata:{}},{id:'milk',text:'fresh dairy milk',metadata:{}}]});
  const model={status:semantic.status,available:semantic.body?.available===true,
    ranked_ids:semantic.body?.results?.map(x=>x.id),finite_scores:semantic.body?.results?.every(x=>Number.isFinite(x.semantic_score))};
  const result={profiles,cross_profile_status:denied.status,unauthorized_status:unauthorized.status,model,tls_verified:true,workflow_executions:0};
  console.log(JSON.stringify(result));
  if(profiles.some(x=>x.status!==200||!x.empty)||denied.status!==403||unauthorized.status!==401||model.status!==200
    ||!model.available||!model.finite_scores||model.ranked_ids?.[0]!=='boots')process.exit(1);
})().catch(e=>{console.log(JSON.stringify({failed:true,error:e.name,cause:e.cause?.code}));process.exit(1)});
'''


if __name__ == '__main__':
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise SystemExit('Run as root on home-core')
    path = Path('/var/lib/homecompute-openclaw/config/workflow-retrieval.json')
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise SystemExit('Private profile configuration required')
    result = subprocess.run(['docker', 'exec', '-i', 'homecompute-automation-n8n-1', 'node', '-e', PROBE],
        input=path.read_text(), capture_output=True, text=True, timeout=60)
    print(result.stdout.strip())
    if result.returncode:
        raise SystemExit('Private retrieval route/model verification failed')
