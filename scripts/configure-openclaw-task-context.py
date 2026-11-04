#!/usr/bin/env python3
"""Plan/apply one retained model context allowance, preserving authority and data."""
import argparse
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import uuid

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('atlas_deploy',ROOT/'scripts/deploy-openclaw-atlas-tasks.py')
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
TARGET=65536
ENV=Path('/etc/homecompute-openclaw/agent-openclaw.env')


def projection(config):
    models=config['models']['providers']['inference']['models']
    if len(models)!=1 or models[0]['id']!='automation-moe' or models[0].get('maxTokens')!=4096:
        raise ValueError('Retained model/output contract required')
    result=copy.deepcopy(config);result['models']['providers']['inference']['models'][0]['contextWindow']='<reviewed-context>'
    return result


def environment(raw,target):
    lines=raw.decode().splitlines()
    if sum(x.startswith('NEMOCLAW_CONTEXT_WINDOW=') for x in lines)!=1:raise ValueError('Exact declared context setting required')
    return ('\n'.join('NEMOCLAW_CONTEXT_WINDOW='+str(target) if x.startswith('NEMOCLAW_CONTEXT_WINDOW=') else x for x in lines)+'\n').encode()


def plan(revision,capacity,backup=None):
    P.source_guard(revision)
    declared=json.loads((ROOT/'config/openclaw-nemoclaw.json').read_bytes())['models']['providers']['inference']['models'][0]
    template=(ROOT/'config/openclaw-nemoclaw.env.example').read_text().splitlines()
    if declared.get('contextWindow')!=TARGET or declared.get('contextTokens')!=TARGET or template.count('NEMOCLAW_CONTEXT_WINDOW='+str(TARGET))!=1:
        raise ValueError('Tracked task context declaration differs')
    u,ctx=P.guest_context()
    raw=P.guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-context-config')
    cfg=json.loads(raw);projection(cfg);old=cfg['models']['providers']['inference']['models'][0]['contextWindow']
    if old not in (32768,TARGET):raise ValueError('Reviewed retained context preimage required')
    env=u.SUP.private_file(ENV)
    if u.environment().get('NEMOCLAW_CONTEXT_WINDOW')!=str(old):raise ValueError('Native/env context differs')
    target=TARGET
    if backup is not None:
        if backup.parent!=ctx.state or backup.is_symlink():raise ValueError('Owned context backup required')
        before=json.loads(u.SUP.private_file(backup/'before-config.json'))
        if u.SUP.mcp_authority(projection(cfg))!=u.SUP.mcp_authority(projection(before)):raise ValueError('Later authority requires reconciliation')
        target=before['models']['providers']['inference']['models'][0]['contextWindow']
    elif capacity!=262144:raise ValueError('Explicit verified upstream context capacity required')
    if target not in (32768,TARGET) or target>capacity:raise ValueError('Qualified backend capacity required')
    after=copy.deepcopy(cfg);after['models']['providers']['inference']['models'][0]['contextWindow']=target
    result={'source_revision':revision,'sandbox_id':ctx.uuid,'generation':ctx.generation,'before_context':old,
            'after_context':target,'backend_context_verified':capacity,'source_config_sha256':P.sha(raw),
            'source_env_sha256':P.sha(env),'version_changed':False,'rollback':backup is not None}
    result['plan_sha256']=P.sha(P.canonical(result))
    return result,u,ctx,raw,after,env


def configure(revision,capacity,expected=None,apply=False,rollback=None):
    result,u,ctx,raw,after,env=plan(revision,capacity,rollback)
    if not apply:return result
    if expected!=result['plan_sha256']:raise ValueError('Reviewed context plan changed')
    tx=ctx.state/'atlas-context-transaction.json'
    if tx.exists() and json.loads(u.SUP.private_file(tx)).get('status')!='complete':raise ValueError('Context transaction needs operator review')
    if result['before_context']==result['after_context']:return {**result,'applied':True,'changed':False}
    locks=[];stopped=False
    try:
        for name in ('atlas-context.lock','atlas-tasks.lock','error-recovery.lock','managed-upgrade.lock','mcp-binding.lock'):
            f=open(ctx.state/name,'a');locks.append(f);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        current,_,_,again,_,again_env=plan(revision,capacity,rollback)
        if current!=result or again!=raw or again_env!=env:raise ValueError('Context inputs changed under lock')
        backup=ctx.state/('atlas-context-backup-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
        P.private_write(backup/'before-config.json',raw);P.private_write(backup/'before-env',env)
        P.private_write(backup/'plan.json',P.canonical(result))
        source_stage=P.guest_stage(ctx,revision)
        record={**result,'backup':str(backup),'status':'active','stage':'prepared','public_source_stage':source_stage};P.private_write(tx,P.canonical(record))
        if P.run(['systemctl','--user','is-active','homecompute-openclaw-supervisor.service']).decode().strip()!='active':raise ValueError('Existing active watchdog required')
        P.run(['systemctl','--user','stop','homecompute-openclaw-supervisor.service']);stopped=True
        u.require_idle(ctx,{})
        changes=[{'path':'models.providers.inference.models','value':after['models']['providers']['inference']['models']}]
        command=['docker','exec','--user',str(P.SANDBOX_UID),'--env','HOME=/sandbox',ctx.container,'openclaw','config','set','--batch-json',json.dumps(changes)]
        P.guest_command(ctx,command+['--dry-run'],'atlas-context-native-validation')
        if P.guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-context-pre-mutation')!=raw:raise ValueError('Native context changed before apply')
        P.guest_command(ctx,command,'atlas-context-native-apply')
        actual=json.loads(P.guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-context-after-config'))
        if actual!=after:raise ValueError('Context changed unrelated configuration')
        u.replace(ENV,environment(env,result['after_context']));ctx.env['NEMOCLAW_CONTEXT_WINDOW']=str(result['after_context'])
        record['stage']='context-and-env-applied';P.private_write(tx,P.canonical(record))
        P.guest_command(ctx,[ctx.cli,u.SUP.NAME,'mcp','restart','homecompute'],'atlas-context-refresh-gateway')
        actual=json.loads(P.guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-context-final-config'))
        if u.SUP.mcp_authority(actual)!=u.SUP.mcp_authority(after):raise ValueError('Context authority changed after refresh')
        u.verify_runtime(ctx,actual);tools=u.verify_mcp_client(ctx)
        ctx.check_registry();ctx.check_container()
        if ctx.observe()!='Ready':raise ValueError('Retained context runtime not ready')
        if u.SUP.private_file(ENV)!=environment(env,result['after_context']):raise ValueError('Context env readback failed')
        record.update(status='complete',stage='verified',mcp_tools=tools);P.private_write(tx,P.canonical(record))
        P.private_write(backup/'completed.json',P.canonical(record))
        return {**result,'applied':True,'verified':True,'mcp_tools':tools,'backup':str(backup),'public_source_stage':source_stage}
    except Exception:
        if 'record' in locals():record['status']='failed';P.private_write(tx,P.canonical(record))
        raise
    finally:
        if stopped:P.run(['systemctl','--user','start','homecompute-openclaw-supervisor.service'])
        for f in locks:f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision',required=True)
    p.add_argument('--verified-backend-context',type=int,required=True);p.add_argument('--apply',action='store_true')
    p.add_argument('--expected-plan-sha256');p.add_argument('--rollback',type=Path);a=p.parse_args();os.umask(0o077)
    print(json.dumps(configure(a.revision,a.verified_backend_context,a.expected_plan_sha256,a.apply,a.rollback),sort_keys=True))
