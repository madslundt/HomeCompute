#!/usr/bin/env python3
"""Guarded, retained-version Atlas task rollout; defaults to a read-only plan."""
from __future__ import annotations
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BASELINE = '12925c9b6d60dd9881a224d29ae45b0a79610346'
SANDBOX_UID = 998  # The retained managed image's sandbox principal; host operator is UID 1000.
CORE = Path('/opt/homecompute/openclaw-communication/scripts')
TARGETS = ('openclaw_household_tasks.py', 'openclaw_task_location.py',
           'openclaw-household-task-reminders.py', 'openclaw_task_followups.py')
SKILLS = ('task-ownership', 'tasks-and-reminders', 'meal-planning', 'household-paperwork', 'budget-analysis')
GUEST_FILES = ('scripts/verify-openclaw-atlas-handoff.py', 'scripts/verify-openclaw-workflow-skills.py',
 'scripts/setup-openclaw-team.py', 'scripts/openclaw_team_policy.py',
 'scripts/openclaw_access_policy.py', 'scripts/openclaw_integration_policy.py',
 'scripts/setup-openclaw-workflow-skills.py', 'scripts/openclaw_workflow_skills.py',
 'config/openclaw-team.json', 'config/openclaw-routines.json',
 *('deploy/openclaw/skills/' + name + '/SKILL.md' for name in SKILLS))
FILES = ('scripts/deploy-openclaw-atlas-tasks.py', 'scripts/configure-openclaw-task-context.py',
         'config/openclaw-nemoclaw.env.example', 'config/openclaw-nemoclaw.json', *('scripts/' + n for n in TARGETS),
         'scripts/upgrade-openclaw-nemoclaw.py', 'scripts/supervise-openclaw-nemoclaw.py',
         'scripts/restore-openclaw-nemoclaw.py', 'scripts/recover-openclaw-team.py',
         'scripts/prepare-openclaw-upgrade-assets.py', *GUEST_FILES)
STATE = Path('/var/lib/homecompute-atlas-tasks')
PREFIX = 'homecompute-openclaw-'
TIMERS = tuple(PREFIX + n + '.timer' for n in ('household-task-reminders', 'family-task-reminders'))
SENDERS = tuple(PREFIX + n + '.service' for n in ('household-task-reminders', 'family-task-reminders'))
SERVICES = (PREFIX + 'adapter.service', PREFIX + 'location-reminders.service')


def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
def run(argv, **kwargs): return subprocess.run(argv, check=True, capture_output=True, timeout=30, **kwargs).stdout


def private_write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.atlas-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally: Path(temp).unlink(missing_ok=True)


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def protected(path, *, optional=False):
    if any(p.is_symlink() for p in (path, *path.parents)): raise ValueError('Protected path contains symlink')
    if optional and not path.exists(): return None
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022 or s.st_nlink != 1 or s.st_size > 2*1024**2:
        raise ValueError('Bounded root-owned source required')
    return path.read_bytes()


def source_guard(revision):
    if not re.fullmatch('[0-9a-f]{40}', revision) or ROOT != Path('/srv/homecompute/releases') / revision:
        raise ValueError('Exact release directory required')
    for p in (ROOT, *ROOT.parents):
        s = p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('Root-protected release required')
    def git(*args): return run(['git', '-c', 'safe.directory='+str(ROOT), '-C', str(ROOT), *args])
    if git('rev-parse', 'HEAD').decode().strip() != revision: raise ValueError('Release identity differs')
    git('merge-base', '--is-ancestor', revision, 'origin/master')
    h = hashlib.sha256()
    for name in sorted(set(FILES)):
        data = protected(ROOT/name)
        if data != git('show', revision+':'+name): raise ValueError('Unpublished rollout source')
        h.update(name.encode()+b'\0'+hashlib.sha256(data).digest())
    return h.hexdigest()


def unit(name):
    out = run(['systemctl', 'show', name, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'FragmentPath']).decode()
    d = dict(x.split('=', 1) for x in out.splitlines() if '=' in x)
    path = Path('/etc/systemd/system.attached') / name
    if d.get('LoadState') != 'loaded' or d.get('ActiveState') not in {'active', 'inactive'} or d.get('FragmentPath') != str(path):
        raise ValueError('Existing stable communication unit required')
    return {'active': d['ActiveState'] == 'active', 'unit_sha256': sha(protected(path))}


def database_counts():
    directory = Path('/var/lib/homecompute-openclaw/communication/adapter')
    result = {}
    for name in ('household-tasks.sqlite3', 'family-tasks.sqlite3', 'transport.sqlite3'):
        p = directory / name
        if p.is_symlink() or not p.is_file(): raise ValueError('Existing task ledgers required')
        with sqlite3.connect('file:'+str(p)+'?mode=ro', uri=True, timeout=3) as db:
            db.execute('PRAGMA query_only=ON')
            if name == 'transport.sqlite3':
                if db.execute("SELECT count(*) FROM turns WHERE state='running'").fetchone()[0] or db.execute("SELECT count(*) FROM events WHERE state='sending'").fetchone()[0]:
                    raise ValueError('Communication work is active')
            else:
                result[name] = {t: db.execute('SELECT count(*) FROM '+t).fetchone()[0]
                                for t in ('household_tasks', 'household_task_requests', 'household_task_events')}
    return result


def core_plan(revision):
    if os.geteuid() != 0 or socket.gethostname() != 'home-core': raise ValueError('Core root lifecycle required')
    bundle = source_guard(revision); rows = []
    for name in TARGETS:
        current = protected(CORE/name, optional=name == 'openclaw_task_followups.py')
        target = protected(ROOT/'scripts'/name)
        old = None if name == 'openclaw_task_followups.py' else run(['git','-C',str(ROOT),'show',BASELINE+':scripts/'+name])
        if current not in (old, target): raise ValueError('Unreviewed installed task module')
        compile(target, name, 'exec')
        rows.append({'name': name, 'before': sha(current) if current is not None else None, 'after': sha(target)})
    states = {name: unit(name) for name in (*TIMERS, *SENDERS, *SERVICES)}
    if any(states[n]['active'] for n in SENDERS): raise ValueError('Wait for reminder dispatch to finish')
    for n in TIMERS:
        p = Path('/etc/systemd/system.attached/timers.target.wants')/n
        if not p.is_symlink() or p.readlink() != Path('/etc/systemd/system.attached')/n:
            raise ValueError('Persistent task timer boot ownership required')
    counts = database_counts()
    result = {'source_revision': revision, 'bundle_sha256': bundle, 'modules': rows,
              'service_state': states, 'task_counts': counts, 'timer_boot_ownership_verified': True}
    result['plan_sha256'] = sha(canonical(result)); return result


def control(action, name): run(['systemctl', action, name])


def stop_core(states):
    for n in TIMERS: control('stop', n)
    deadline = time.monotonic()+60
    while any(unit(n)['active'] for n in SENDERS):
        if time.monotonic() > deadline: raise ValueError('Reminder dispatch did not settle')
        time.sleep(1)
    for n in SERVICES:
        if states[n]['active']: control('stop', n)
    database_counts()


def resume_core(states):
    for n in (*SERVICES, *TIMERS):
        if states[n]['active']: control('start', n)


def core_apply(revision, expected):
    reviewed = core_plan(revision)
    if reviewed['plan_sha256'] != expected: raise ValueError('Reviewed core plan changed')
    STATE.mkdir(mode=0o700, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Private rollout state required')
    with open(STATE/'deployment.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = core_plan(revision)
        if plan['plan_sha256'] != expected: raise ValueError('Reviewed core plan changed')
        if not any(r['before'] != r['after'] for r in plan['modules']): return {**plan, 'applied': True, 'changed': False}
        backup = STATE / ('backup-'+uuid.uuid4().hex); backup.mkdir(mode=0o700)
        private_write(backup/'plan.json', canonical(plan))
        before = {n: protected(CORE/n, optional=True) for n in TARGETS}
        for n, data in before.items():
            if data is not None: private_write(backup/n, data)
        try:
            stop_core(plan['service_state'])
            if database_counts() != plan['task_counts']: raise ValueError('Task records changed before rollout')
            for n in ('household-tasks.sqlite3','family-tasks.sqlite3'):
                with sqlite3.connect('file:/var/lib/homecompute-openclaw/communication/adapter/'+n+'?mode=ro', uri=True) as db:
                    with sqlite3.connect(backup/n) as out: db.backup(out)
                os.chmod(backup/n, 0o600)
            for n in ('openclaw_task_followups.py', *TARGETS[:-1]):
                private_write(CORE/n, (ROOT/'scripts'/n).read_bytes()); os.chmod(CORE/n, 0o644)
            code = "import sys,pathlib;sys.path.insert(0,'/opt/homecompute/openclaw-communication/scripts');from openclaw_household_tasks import HouseholdTasks,FamilyTasks;r=pathlib.Path('/var/lib/homecompute-openclaw/communication/adapter');[t(r) for t in (HouseholdTasks,FamilyTasks)]"
            run(['runuser','-u','homecompute-openclaw','--','/run/current-system/sw/bin/python3','-I','-c',code])
            if database_counts() != plan['task_counts']: raise ValueError('Task records changed during rollout')
            if any(protected(CORE/r['name']) != (ROOT/'scripts'/r['name']).read_bytes() for r in plan['modules']): raise ValueError('Task code readback differs')
            private_write(backup/'completed.json', canonical({'verified': True, 'revision': revision}))
        except Exception:
            for n, data in before.items():
                if data is None: (CORE/n).unlink(missing_ok=True)
                else: private_write(CORE/n, data); os.chmod(CORE/n, 0o644)
            raise
        finally: resume_core(plan['service_state'])
        return {'applied': True, 'verified': True, 'source_revision': revision, 'backup': str(backup),
                'task_records_preserved': True, 'timer_boot_ownership_verified': True}


def guest_owner(ctx):
    code, raw = ctx.run(['docker','exec',ctx.container,'stat','-c','%u:%g','/sandbox/.openclaw'], 'atlas-native-owner')
    if code or raw.decode().strip() != str(SANDBOX_UID)+':'+str(SANDBOX_UID):
        raise ValueError('Retained native sandbox principal required')
    return SANDBOX_UID


def guest_context(*, recovery=False):
    if os.getuid() != 1000 or socket.gethostname() != 'agents': raise ValueError('Existing operator guest required')
    u = module('upgrade-openclaw-nemoclaw'); ctx = u.SUP.Supervisor(u.environment())
    if ctx.revision != u.SUP.REVISION: raise ValueError('Retained version required')
    ctx.check_registry(); ctx.check_container(); guest_owner(ctx)
    if ctx.observe() != 'Ready' or not ctx.ready(): raise ValueError('Healthy retained runtime required')
    transaction = ctx.state/'atlas-tasks-transaction.json'
    if not recovery and transaction.exists() and json.loads(transaction.read_bytes()).get('status') != 'complete':
        raise ValueError('Atlas task deployment needs operator reconciliation')
    u.require_idle(ctx, {})
    return u, ctx


def guest_command(ctx, argv, label):
    code, raw = ctx.run(argv, label, timeout=120)
    if code:
        safe=re.sub('[^a-zA-Z0-9_.-]','_',label)[:128]
        private_write(ctx.state/('atlas-task-failed-'+safe+'.log'),raw)
        raise ValueError('Scoped native task operation failed: '+label)
    return raw


def guest_plan(revision):
    bundle = source_guard(revision); u, ctx = guest_context()
    raw = guest_command(ctx, ['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'], 'atlas-read-config')
    cfg = json.loads(raw)
    if len(cfg['agents']['entries']) != 12 or cfg.get('cron',{}).get('enabled') is not False:
        raise ValueError('Retained complete roster and disabled cron required')
    if cfg['agents']['defaults']['subagents'].get('maxConcurrent') != 1 or cfg['agents']['defaults']['subagents'].get('maxSpawnDepth') != 1:
        raise ValueError('Serial depth-one delegation required')
    state_files = {}
    for role, entry in cfg['agents']['entries'].items():
        path = '/sandbox/.openclaw/'+('workspace' if role == 'main' else 'workspace-'+role)+'/AGENTS.md'
        if entry['workspace'] != str(Path(path).parent): raise ValueError('Canonical workspaces required')
        state_files[path] = sha(guest_command(ctx,['docker','exec',ctx.container,'cat',path],'atlas-read-identity'))
    result = {'source_revision': revision,'bundle_sha256': bundle,'source_config_sha256':sha(raw),
              'sandbox_id':ctx.uuid,'generation':ctx.generation,'identity_hashes':state_files,'roles':12,
              'version_changed':False,'cron_enabled':False}
    result['plan_sha256']=sha(canonical(result));return result


def guest_files(ctx, config):
    paths = ['team-skill-ownership.json']
    for role in config['agents']['entries']:
        workspace = 'workspace' if role == 'main' else 'workspace-'+role
        paths.append(workspace+'/AGENTS.md')
    for role, (skill, _) in module('openclaw_workflow_skills').WORKFLOWS.items():
        workspace = 'workspace' if role == 'main' else 'workspace-'+role
        paths.append(workspace+'/skills/'+skill+'/SKILL.md')
    code = """import pathlib,json,base64,stat,os
root=pathlib.Path('/sandbox/.openclaw');result={}
for name in json.loads(__import__('sys').argv[1]):
 p=root/name
 if any(x.is_symlink() for x in (p,*p.parents)):raise ValueError('Symlink refused')
 if not p.exists():result[name]=None;continue
 st=p.stat()
 if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_size>1048576:raise ValueError('Scoped file refused')
 result[name]={'data':base64.b64encode(p.read_bytes()).decode(),'mode':stat.S_IMODE(st.st_mode)}
print(json.dumps(result))"""
    raw=guest_command(ctx,['docker','exec','--user',str(SANDBOX_UID),'--env','HOME=/sandbox',ctx.container,'python3','-c',code,json.dumps(paths)],'atlas-scoped-file-images')
    return json.loads(raw)


def guest_stage(ctx, revision):
    stage='/tmp/homecompute-atlas-tasks-'+uuid.uuid4().hex
    guest_command(ctx,['docker','exec','--user',str(SANDBOX_UID),'--env','HOME=/sandbox',ctx.container,'mkdir','-m','700',stage],'atlas-stage-source')
    archive=run(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'archive',revision,*GUEST_FILES])
    run(['docker','exec','-i','--user',str(SANDBOX_UID),'--env','HOME=/sandbox',ctx.container,'tar','-x','-C',stage],input=archive)
    return stage


def guest_apply(revision, expected):
    u, ctx = guest_context()
    with open(ctx.state/'atlas-tasks.lock','a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan=guest_plan(revision)
        if plan['plan_sha256'] != expected: raise ValueError('Reviewed guest plan changed')
        # Existing recovery/deployment fences are shared; never race a lifecycle.
        locks=[]
        try:
            for name in ('error-recovery.lock','managed-upgrade.lock','mcp-binding.lock'):
                f=open(ctx.state/name,'a');locks.append(f);fcntl.flock(f,fcntl.LOCK_EX | fcntl.LOCK_NB)
            backup=ctx.state/('atlas-tasks-backup-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
            private_write(backup/'plan.json',canonical(plan))
            raw=guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-backup-config')
            private_write(backup/'openclaw-before.json',raw);before=json.loads(raw)
            for i,path in enumerate(plan['identity_hashes']):
                private_write(backup/('identity-'+str(i)),guest_command(ctx,['docker','exec',ctx.container,'cat',path],'atlas-backup-identity'))
            private_write(backup/'before-files.json',canonical(guest_files(ctx,before)))
            transaction=ctx.state/'atlas-tasks-transaction.json'
            record={'status':'active','backup':str(backup),'source_revision':revision,'stage':'prepared'}
            private_write(transaction,canonical(record))
            stage=guest_stage(ctx,revision)
            watchdog=run(['systemctl','--user','is-active','homecompute-openclaw-supervisor.service']).decode().strip()
            if watchdog!='active':raise ValueError('Existing active supervisor required')
            run(['systemctl','--user','stop','homecompute-openclaw-supervisor.service'])
            u.require_idle(ctx,{})
            def command(script,*args):
                raw=guest_command(ctx,['docker','exec','--user',str(SANDBOX_UID),'--env','HOME=/sandbox',ctx.container,'python3',stage+'/scripts/'+script,*args],'atlas-'+script)
                return json.JSONDecoder().raw_decode(raw.decode()[raw.index(b'{'):])[0]
            workflow=command('setup-openclaw-workflow-skills.py','--source',stage)
            command('setup-openclaw-workflow-skills.py','--source',stage,'--apply','--expected-plan-sha256',workflow['plan_sha256'])
            after_raw=guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-read-skills')
            args=['--manifest',stage+'/config/openclaw-team.json','--coordinator-identity-only','--expected-config-sha256',sha(after_raw)]
            for role in before['agents']['entries']: args += ['--identity-role',role]
            command('setup-openclaw-team.py',*args)
            command('setup-openclaw-team.py',*args,'--apply')
            after=json.loads(guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-final-config'))
            normalized=copy.deepcopy(after)
            for role in normalized['agents']['entries']:
                normalized['agents']['entries'][role]['skills']=before['agents']['entries'][role]['skills']
            if normalized != before or 'task-ownership' not in after['agents']['entries']['main']['skills']:
                raise ValueError('Atlas rollout expanded unrelated authority')
            private_write(backup/'after-files.json',canonical(guest_files(ctx,after)))
            private_write(backup/'after-config.json',canonical(after))
            record['stage']='instructions-and-skills-applied';private_write(transaction,canonical(record))
            # Native supported MCP refresh reloads the gateway and updated tool schemas.
            guest_command(ctx,[ctx.cli,u.SUP.NAME,'mcp','restart','homecompute'],'atlas-refresh-mcp-schemas')
            u.verify_runtime(ctx,after); tools=u.verify_mcp_client(ctx)
            ctx.check_registry();ctx.check_container()
            if ctx.observe()!='Ready': raise ValueError('Retained identity lost readiness')
            receipt={'verified':True,'applied':True,'source_revision':revision,'version_changed':False,
                     'mcp_tools':tools,'roles':12,'backup':str(backup),'workflow_plan':workflow['plan_sha256'],'public_source_stage':stage}
            private_write(backup/'completed.json',canonical(receipt))
            record.update(status='complete',stage='verified');private_write(transaction,canonical(record))
            return receipt
        except Exception:
            if 'record' in locals():
                record['status']='failed';private_write(transaction,canonical(record))
            raise
        finally:
            if 'watchdog' in locals() and watchdog=='active':
                run(['systemctl','--user','start','homecompute-openclaw-supervisor.service'])
            for f in locks:f.close()


def guest_reconcile_unmodified(revision, backup, *, apply=False, expected=None):
    source_guard(revision);u,ctx=guest_context(recovery=True)
    if backup.parent!=ctx.state or backup.is_symlink():raise ValueError('Owned failed backup required')
    transaction=ctx.state/'atlas-tasks-transaction.json'
    record=json.loads(u.SUP.private_file(transaction));saved=json.loads(u.SUP.private_file(backup/'plan.json'))
    if (record.get('status')!='failed' or record.get('stage')!='prepared' or record.get('backup')!=str(backup)
            or record.get('source_revision')!=saved.get('source_revision')
            or saved.get('sandbox_id')!=ctx.uuid or saved.get('generation')!=ctx.generation):
        raise ValueError('Exact failed preparation checkpoint required')
    before=u.SUP.private_file(backup/'openclaw-before.json')
    files=json.loads(u.SUP.private_file(backup/'before-files.json'))
    def unchanged():
        raw=guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-reconcile-unmodified-config')
        if raw!=before or guest_files(ctx,json.loads(before))!=files:
            raise ValueError('Native state changed; failed transaction requires recovery')
    unchanged()
    result={'backup':str(backup),'failed_source_revision':saved['source_revision'],'source_revision':revision,
            'native_state_unchanged':True,'sandbox_id':ctx.uuid,'generation':ctx.generation}
    result['plan_sha256']=sha(canonical(result))
    if apply:
        if expected!=result['plan_sha256']:raise ValueError('Unmodified reconciliation plan changed')
        locks=[]
        try:
            for name in ('atlas-tasks.lock','error-recovery.lock','managed-upgrade.lock','mcp-binding.lock'):
                f=open(ctx.state/name,'a');locks.append(f);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if json.loads(u.SUP.private_file(transaction))!=record:raise ValueError('Failure checkpoint changed')
            unchanged()
            private_write(transaction,canonical({**record,'status':'complete','reconciled_unmodified':True,'reconciliation_source':revision}))
        finally:
            for f in locks:f.close()
    return {**result,'applied':apply}


def core_rollback(revision, backup, *, apply=False, expected=None):
    if os.getuid()!=0 or socket.gethostname()!='home-core':raise ValueError('Core root required')
    source_guard(revision)
    if backup.parent!=STATE or backup.is_symlink():raise ValueError('Owned backup required')
    saved=json.loads(protected(backup/'plan.json'))
    if saved['source_revision']!=revision:raise ValueError('Selected backup release differs')
    rows=[]
    for row in saved['modules']:
        name=row['name']
        if name not in TARGETS:raise ValueError('Backup target refused')
        old=protected(backup/name,optional=row['before'] is None)
        current=protected(CORE/name,optional=True)
        if (sha(old) if old is not None else None)!=row['before'] or (sha(current) if current is not None else None) not in (row['before'],row['after']):
            raise ValueError('Later code requires reconciliation')
        rows.append((name,old))
    if {x[0] for x in rows}!=set(TARGETS):raise ValueError('Complete scoped backup required')
    for name in ('household-tasks.sqlite3','family-tasks.sqlite3'):
        with sqlite3.connect('file:/var/lib/homecompute-openclaw/communication/adapter/'+name+'?mode=ro',uri=True) as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(household_task_reminders)')}
            if 'purpose' in columns and db.execute("SELECT count(*) FROM household_task_reminders WHERE purpose='follow_up' AND state IN ('pending','sending','uncertain')").fetchone()[0]:
                raise ValueError('Reconcile pending follow-ups before code rollback')
            for (body,) in db.execute('SELECT body FROM household_tasks'):
                task=json.loads(body)
                if task['status']=='open' and task.get('follow_up_at'):raise ValueError('Disable saved follow-up watches before rollback')
    states={name:unit(name) for name in (*TIMERS,*SENDERS,*SERVICES)}
    if any(states[n]['active'] for n in SENDERS):raise ValueError('Wait for dispatcher')
    result={'backup':str(backup),'source_revision':revision,'service_state':states,'database_counts':database_counts(),'rollback':True}
    result['plan_sha256']=sha(canonical(result))
    if apply:
        if expected!=result['plan_sha256']:raise ValueError('Rollback plan changed')
        with open(STATE/'deployment.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fresh=core_rollback(revision,backup)
            if fresh['plan_sha256']!=expected:raise ValueError('Rollback changed under lock')
            try:
                stop_core(states)
                for name,data in rows:
                    if data is None:(CORE/name).unlink(missing_ok=True)
                    else:private_write(CORE/name,data);os.chmod(CORE/name,0o644)
            finally:resume_core(states)
    return {**result,'applied':apply}


def guest_rollback(revision, backup, *, apply=False, expected=None):
    source_guard(revision);u,ctx=guest_context(recovery=True)
    if backup.parent!=ctx.state or backup.is_symlink():raise ValueError('Owned guest backup required')
    saved=json.loads((backup/'plan.json').read_bytes())
    if saved['source_revision']!=revision or saved['sandbox_id']!=ctx.uuid or saved['generation']!=ctx.generation:
        raise ValueError('Exact retained backup identity required')
    original=json.loads((backup/'openclaw-before.json').read_bytes())
    installed=json.loads((backup/'after-config.json').read_bytes())
    current=json.loads(guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-rollback-read-config'))
    if u.SUP.mcp_authority(current)!=u.SUP.mcp_authority(installed):raise ValueError('Later native configuration requires reconciliation')
    files=guest_files(ctx,current);wanted=json.loads((backup/'before-files.json').read_bytes())
    if files!=json.loads((backup/'after-files.json').read_bytes()):raise ValueError('Later workspace edits require reconciliation')
    result={'source_revision':revision,'backup':str(backup),'current_config_sha256':sha(canonical(current)),
            'files_sha256':sha(canonical(files)),'rollback':True}
    result['plan_sha256']=sha(canonical(result))
    if apply:
        if expected!=result['plan_sha256']:raise ValueError('Reviewed guest rollback changed')
        with open(ctx.state/'atlas-tasks.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            transaction=ctx.state/'atlas-tasks-transaction.json'
            private_write(transaction,canonical({'status':'active','rollback':True,'backup':str(backup)}))
            stage=guest_stage(ctx,revision)
            # Every fixed file is guarded by its just-reviewed after image.
            code="""import pathlib,json,base64,os,tempfile,sys,stat
root=pathlib.Path('/sandbox/.openclaw');payload=json.load(sys.stdin)
for name,expected in payload['current'].items():
 p=root/name
 if any(x.is_symlink() for x in (p,*p.parents)):raise ValueError('Symlink refused')
 actual={'data':base64.b64encode(p.read_bytes()).decode(),'mode':stat.S_IMODE(p.stat().st_mode)} if p.exists() else None
 if actual!=expected:raise ValueError('Rollback file changed')
for name,value in payload['wanted'].items():
 p=root/name
 if value is None:p.unlink(missing_ok=True);continue
 fd,temp=tempfile.mkstemp(dir=p.parent)
 with os.fdopen(fd,'wb') as f:f.write(base64.b64decode(value['data']));f.flush();os.fsync(f.fileno())
 os.chmod(temp,value['mode']);os.replace(temp,p)
"""
            run(['systemctl','--user','stop','homecompute-openclaw-supervisor.service'])
            try:
                u.require_idle(ctx,{})
                run(['docker','exec','-i','--user',str(SANDBOX_UID),'--env','HOME=/sandbox',ctx.container,'python3','-c',code],input=canonical({'current':files,'wanted':wanted}))
                changes=[{'path':'agents.entries.'+role+'.skills','value':entry['skills']} for role,entry in original['agents']['entries'].items()
                         if current['agents']['entries'][role]['skills']!=entry['skills']]
                guest_command(ctx,['docker','exec','--user',str(SANDBOX_UID),'--env','HOME=/sandbox',ctx.container,'openclaw','config','set','--batch-json',json.dumps(changes)],'atlas-rollback-native-skills')
                guest_command(ctx,[ctx.cli,u.SUP.NAME,'mcp','restart','homecompute'],'atlas-rollback-refresh')
                after=json.loads(guest_command(ctx,['docker','exec',ctx.container,'cat','/sandbox/.openclaw/openclaw.json'],'atlas-rollback-readback'))
                if u.SUP.mcp_authority(after)!=u.SUP.mcp_authority(original) or guest_files(ctx,after)!=wanted:raise ValueError('Rollback verification failed')
                u.verify_runtime(ctx,after);u.verify_mcp_client(ctx)
                private_write(ctx.state/'atlas-tasks-transaction.json',canonical({'status':'complete','rolled_back':True,'backup':str(backup)}))
            except Exception:
                private_write(transaction,canonical({'status':'failed','rollback':True,'backup':str(backup)}))
                raise
            finally:run(['systemctl','--user','start','homecompute-openclaw-supervisor.service'])
    return {**result,'applied':apply}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('host',choices=('core','guest'));p.add_argument('--revision',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--expected-plan-sha256')
    p.add_argument('--rollback',type=Path)
    p.add_argument('--reconcile-unmodified',type=Path)
    a=p.parse_args();os.umask(0o077)
    if a.reconcile_unmodified:
        if a.host!='guest' or a.rollback:raise ValueError('Only an unmodified failed guest preparation can be reconciled')
        print(json.dumps(guest_reconcile_unmodified(a.revision,a.reconcile_unmodified,apply=a.apply,expected=a.expected_plan_sha256),sort_keys=True));return
    if a.rollback:
        result=(core_rollback if a.host=='core' else guest_rollback)(a.revision,a.rollback,apply=a.apply,expected=a.expected_plan_sha256)
        print(json.dumps(result,sort_keys=True));return
    result=(core_apply if a.host=='core' else guest_apply)(a.revision,a.expected_plan_sha256) if a.apply else (core_plan if a.host=='core' else guest_plan)(a.revision)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__': main()
