#!/usr/bin/env python3
"""Provision fixed owner-only task delivery; preserve Family and all credentials."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
P=load('atlas_delivery_deploy','deploy-openclaw-atlas-tasks.py')
b=load('atlas_delivery_bootstrap','bootstrap-mattermost.py')
sync=load('atlas_delivery_sync','sync-mattermost-atlas.py')
CONFIG=Path('/var/lib/homecompute-openclaw/config')
NAMES=('household-tasks.json','family-tasks.json')


def service_read(path):
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Canonical service configuration required')
    s=path.stat();account=pwd.getpwnam('homecompute-openclaw')
    if s.st_uid!=account.pw_uid or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1:raise ValueError('Private service-owned configuration required')
    return path.read_bytes()


def members(api,channel):
    rows,_=api.request('GET','/channels/'+channel+'/members?page=0&per_page=100')
    if not isinstance(rows,list) or len(rows)>=100:raise ValueError('Bounded destination audience required')
    humans,bots=set(),set()
    for row in rows:
        user,_=api.request('GET','/users/'+b.identity({'id':row['user_id']},'member'))
        (bots if user.get('is_bot') is True else humans).add(b.identity(user,'member'))
    return humans,bots


def audience(humans,bots,expected_humans,allowed_bots,delivery_bot):
    if humans!=set(expected_humans) or not bots<=set(allowed_bots) or delivery_bot not in bots:
        raise ValueError('Fixed destination audience differs')


def plan(revision):
    core=P.core_plan(revision)
    policy=json.loads((ROOT/'config/openclaw-task-delivery.json').read_bytes())
    if policy['schema_version']!=1 or policy['owner_channel']['name']!='owner-tasks' or policy['owner_channel']['human_audience']!='owner_only' or policy['family_channel']!={'name':'family','human_audience':'approved_adults'}:raise ValueError('Fixed delivery policy required')
    creds=b.read_private(b.PRIVATE/'credentials.json');access=b.read_private(b.PRIVATE/'household-access.json')
    owner=b.identity({'id':access['owner_id']},'owner');partner=b.identity({'id':access['partner_id']},'partner');team=b.identity({'id':access['team_id']},'team')
    if creds['operator_id']!=owner:raise ValueError('Retained owner identity differs')
    api=sync.operator(b,b.PRIVATE,creds,owner)
    before={name:service_read(CONFIG/name) for name in NAMES};cfg={name:json.loads(raw) for name,raw in before.items()}
    for scope,name in [('household',NAMES[0]),('family',NAMES[1])]:
        row=cfg[name]
        if (not {'schema_version','channel_id','bot_id','token_file'}<=set(row) or set(row)-{'schema_version','channel_id','bot_id','token_file','allowed_human_ids','allowed_bot_ids'}
                or row['schema_version']!=1 or row['token_file']!=str(CONFIG/(scope+'-task-mattermost-token'))):raise ValueError('Retained task transport required')
    family=access['family_channel_id']
    if cfg[NAMES[1]]['channel_id']!=family:raise ValueError('Retained Family destination differs')
    room,_=api.request('GET',f'/teams/{team}/channels/name/owner-tasks',missing=True)
    if cfg[NAMES[0]]['channel_id'] not in {family,room['id'] if room else None}:raise ValueError('Unreviewed owner destination')
    allowed_owner_bots={cfg[NAMES[0]]['bot_id'],creds['bot_id']}
    family_humans,family_bots=members(api,family)
    if family_humans!={owner,partner} or cfg[NAMES[1]]['bot_id'] not in family_bots:raise ValueError('Admitted Family audience differs')
    for bot in family_bots|allowed_owner_bots:
        identity,_=api.request('GET','/bots/'+bot)
        if identity.get('owner_id')!=owner:raise ValueError('Unreviewed delivery bot ownership')
    if room:
        if room.get('type')!='P' or room.get('team_id')!=team or room.get('creator_id')!=owner or room.get('delete_at'):raise ValueError('Owner room identity differs')
        humans,bots=members(api,room['id'])
        if humans!={owner} or not bots<=allowed_owner_bots:raise ValueError('Existing owner room audience differs')
    resume_owner=core['service_state'][P.TIMERS[0]]['active']
    maintenance=P.STATE/'owner-destination-maintenance.json'
    if maintenance.exists():
        marker=json.loads(P.protected(maintenance))
        if marker.get('status')=='paused':
            if marker.get('unit')!=P.TIMERS[0] or marker.get('unit_sha256')!=core['service_state'][P.TIMERS[0]]['unit_sha256']:raise ValueError('Destination maintenance ownership differs')
            resume_owner=marker['unit_active_before']
    result={'source_revision':revision,'core_plan_sha256':core['plan_sha256'],'configuration_sha256':{n:P.sha(raw) for n,raw in before.items()},
            'owner_channel_name':'owner-tasks','create_private_owner_room':room is None,'owner_humans':1,'family_humans':2,
            'existing_owner_room':room['id'] if room else None,'resume_owner_timer':resume_owner,'messages_sent':0}
    result['plan_sha256']=P.sha(P.canonical(result))
    data={'api':api,'owner':owner,'partner':partner,'team':team,'room':room,'cfg':cfg,'before':before,'family':family,
          'family_bots':family_bots,'owner_bots':allowed_owner_bots,'core':core,'policy':policy}
    return result,data


def write_config(path,data):
    info=path.stat();fd,tmp=tempfile.mkstemp(prefix='.task-destination-',dir=path.parent)
    try:
        os.fchown(fd,info.st_uid,info.st_gid)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(tmp).unlink(missing_ok=True)


def setup(revision,apply=False,expected=None):
    with b.bootstrap_lock(b.PRIVATE):
        result,d=plan(revision)
        if not apply:return result
        if expected!=result['plan_sha256']:raise ValueError('Reviewed destination plan changed')
        P.STATE.mkdir(mode=0o700,exist_ok=True)
        info=P.STATE.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('Private delivery lifecycle state required')
        with open(P.STATE/'owner-delivery.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            backup=P.STATE/('owner-delivery-backup-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
            P.private_write(backup/'plan.json',P.canonical(result))
            for name,raw in d['before'].items():P.private_write(backup/name,raw)
            success=False;created=False
            try:
                for n in P.TIMERS:P.control('stop',n)
                deadline=time.monotonic()+60
                while any(P.unit(n)['active'] for n in P.SENDERS):
                    if time.monotonic()>deadline:raise ValueError('Dispatch did not settle')
                    time.sleep(1)
                code_plan=P.core_plan(revision)
                code=P.core_apply(revision,code_plan['plan_sha256'])
                api=d['api'];room=d['room']
                if room is None:
                    owner=d['policy']['owner_channel']
                    room,_=api.request('POST','/channels',{'team_id':d['team'],'name':owner['name'],'display_name':owner['display_name'],'purpose':owner['purpose'],'type':'P'})
                    created=True;P.private_write(backup/'created-channel.json',P.canonical({'channel_id':room['id'],'source_revision':revision}))
                channel=b.identity(room,'owner room')
                member,_=api.request('GET',f"/channels/{channel}/members/{d['cfg'][NAMES[0]]['bot_id']}",missing=True)
                if member is None:api.request('POST','/channels/'+channel+'/members',{'user_id':d['cfg'][NAMES[0]]['bot_id']})
                humans,bots=members(api,channel);audience(humans,bots,{d['owner']},d['owner_bots'],d['cfg'][NAMES[0]]['bot_id'])
                humans,bots=members(api,d['family']);audience(humans,bots,{d['owner'],d['partner']},d['family_bots'],d['cfg'][NAMES[1]]['bot_id'])
                after={NAMES[0]:{**d['cfg'][NAMES[0]],'channel_id':channel,'allowed_human_ids':[d['owner']],'allowed_bot_ids':sorted(d['owner_bots'])},
                       NAMES[1]:{**d['cfg'][NAMES[1]],'allowed_human_ids':sorted({d['owner'],d['partner']}),'allowed_bot_ids':sorted(d['family_bots'])}}
                if any(service_read(CONFIG/n)!=raw for n,raw in d['before'].items()):raise ValueError('Task configurations changed during apply')
                for name,value in after.items():write_config(CONFIG/name,(json.dumps(value,indent=2)+'\n').encode())
                for unit in P.SERVICES:
                    if d['core']['service_state'][unit]['active']:P.control('restart',unit)
                for scope,name in [('owner',NAMES[0]),('family',NAMES[1])]:
                    P.run(['runuser','-u','homecompute-openclaw','--','/run/current-system/sw/bin/python3',str(P.CORE/'openclaw-household-task-reminders.py'),'--scope',scope,'--config',str(CONFIG/name),'--state','/var/lib/homecompute-openclaw/communication/adapter','--check'])
                receipt={**result,'applied':True,'verified':True,'created_private_room':created,'channel_id':channel,'backup':str(backup),'core_code':code,
                         'configuration_sha256':{n:P.sha(service_read(CONFIG/n)) for n in NAMES}}
                P.private_write(P.STATE/'owner-task-delivery.json',P.canonical(receipt));P.private_write(backup/'completed.json',P.canonical(receipt))
                marker=P.STATE/'owner-destination-maintenance.json'
                if marker.exists():P.private_write(marker,P.canonical({**json.loads(P.protected(marker)),'status':'complete','source_revision':revision}))
                success=True
                return {'applied':True,'verified':True,'source_revision':revision,'owner_humans':1,'family_humans':2,'private_owner_room_created':created,'backup':str(backup),'messages_sent':0}
            finally:
                if d['core']['service_state'][P.TIMERS[1]]['active']:P.control('start',P.TIMERS[1])
                if success and result['resume_owner_timer']:P.control('start',P.TIMERS[0])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--expected-plan-sha256');a=p.parse_args();os.umask(0o077)
    print(json.dumps(setup(a.revision,a.apply,a.expected_plan_sha256),sort_keys=True))
