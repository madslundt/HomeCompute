#!/usr/bin/env python3
"""Guarded office-only rollout; preserve unrelated release/maintenance state."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
CADDY = 'homecompute-control-plane-caddy-1'
HOMEPAGE = 'homecompute-homepage-homepage-1'
STATE = Path('/var/lib/homecompute-agent-office')
ATTACHED = Path('/etc/systemd/system.attached')
UNITS = ('homecompute-agent-office-firewall.service', 'homecompute-agent-office-proxy.service')


def sha(data): return hashlib.sha256(data).hexdigest()


def call(args, *, data=None, timeout=60):
    result = subprocess.run(args,input=data,text=True,capture_output=True,timeout=timeout)
    if result.returncode:
        raise ValueError('guarded-command-failed:' + Path(args[0]).name)
    return result.stdout


def inspect(name):
    row = json.loads(call(['docker','inspect',name]))[0]
    if not row['State']['Running']:
        raise ValueError('existing-container-not-running')
    return row


def network_identity(row):
    return {name:{k:net.get(k) for k in ('NetworkID','IPAddress','Gateway','Aliases')}
            for name,net in row['NetworkSettings']['Networks'].items()}


def mount(row, destination):
    found=[x for x in row['Mounts'] if x['Destination']==destination]
    if len(found)!=1 or found[0]['Type']!='bind' or found[0]['RW']:
        raise ValueError('exact-readonly-config-mount-required')
    return Path(found[0]['Source'])


def office_patch(caddy, homepage, source_caddy, source_homepage):
    blocks=re.findall(r'^agents\.home\.arpa \{\n.*?^\}\n',source_caddy,re.M|re.S)
    redirects=re.findall(r'\t@agent_office host agents\.home\.arpa\n\thandle @agent_office \{\n.*?\n\t\}\n',source_caddy,re.S)
    cards=re.findall(r'    - Agent office:\n.*?(?=\n    - |\n- |\Z)',source_homepage,re.S)
    if len(blocks)!=1 or len(redirects)!=1 or len(cards)!=1:
        raise ValueError('canonical-office-source-shape')
    block,redirect,card=blocks[0],redirects[0],cards[0]
    if 'agents.home.arpa' in caddy:
        if block not in caddy or redirect not in caddy:
            raise ValueError('existing-office-route-differs')
    else:
        if caddy.count('http://:8080 {\n')!=1:
            raise ValueError('existing-http-listener-shape')
        caddy=caddy.replace('http://:8080 {\n','http://:8080 {\n'+redirect,1)
        caddy=caddy.rstrip()+'\n\n'+block
    if 'homecore-agent-office' in homepage or '    - Agent office:' in homepage:
        if card.strip() not in homepage:
            raise ValueError('existing-office-card-differs')
    else:
        if homepage.count('- AI assistants:\n')!=1:
            raise ValueError('existing-homepage-group-shape')
        homepage=homepage.replace('- AI assistants:\n','- AI assistants:\n'+card.rstrip()+'\n\n',1)
    return caddy,homepage


def protected_file(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or path.is_symlink():
        raise ValueError('root-protected-regular-file-required')
    return path.read_bytes()


def in_place(path, expected, content):
    # Retain the inode: Docker file bind mounts would retain stale content after
    # an atomic rename. The exact preimage is checked again through the open FD.
    fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('regular-file-required')
        with os.fdopen(os.dup(fd),'rb') as source:current=source.read()
        if current!=expected:raise ValueError('config-changed-since-plan')
        os.lseek(fd,0,0);os.ftruncate(fd,0)
        with os.fdopen(os.dup(fd),'wb') as target:
            target.write(content);target.flush();os.fsync(target.fileno())
    finally:os.close(fd)


def release(revision):
    if not re.fullmatch('[0-9a-f]{40}',revision) or ROOT!=Path('/srv/homecompute/releases')/revision:
        raise ValueError('exact-release-directory-required')
    if call(['git','-C',str(ROOT),'rev-parse','HEAD']).strip()!=revision:
        raise ValueError('revision-mismatch')
    if call(['git','-C',str(ROOT),'status','--porcelain','--untracked-files=no']).strip():
        raise ValueError('dirty-release-refused')
    call(['git','-C',str(ROOT),'merge-base','--is-ancestor',revision,'origin/master'])
    protected_file(Path(__file__))


def maintenance_evidence():
    paths=[Path('/var/lib/homecompute/homepage-maintenance')/n for n in ('active-config.json','active-image.json')]
    paths+=list(Path('/var/lib/homecompute-docker-maintenance').glob('*.active'))
    return {str(p):sha(p.read_bytes()) for p in paths if p.exists()}


def plan():
    caddy,homepage=inspect(CADDY),inspect(HOMEPAGE)
    caddy_path=mount(caddy,'/etc/caddy/Caddyfile')
    if caddy_path!=Path('/opt/homecompute/mattermost/control-plane.Caddyfile'):
        raise ValueError('review-current-caddy-path')
    explicit=[x for x in homepage['Mounts'] if x['Destination']=='/app/config/services.yaml']
    if explicit:
        homepage_path=mount(homepage,'/app/config/services.yaml')
        if homepage_path!=Path('/var/lib/homecompute/homepage-maintenance/services.yaml'):
            raise ValueError('review-current-homepage-override')
        manifest=json.loads(protected_file(homepage_path.parent/'active-config.json'))
        expected={'services':{'homepage':{'volumes':[{'read_only':True,'source':str(homepage_path),
                  'target':'/app/config/services.yaml','type':'bind'}]}}}
        if manifest!=expected:raise ValueError('homepage-override-shape-changed')
    else:
        homepage_path=mount(homepage,'/app/config')/'services.yaml'
    before=[protected_file(p) for p in (caddy_path,homepage_path)]
    after=office_patch(*(b.decode() for b in before),
                       (ROOT/'deploy/control-plane/Caddyfile').read_text(),
                       (ROOT/'deploy/homepage/config/services.yaml').read_text())
    return (caddy,homepage),(caddy_path,homepage_path),before,[x.encode() for x in after]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--expected-caddy-sha256')
    parser.add_argument('--expected-homepage-sha256')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!='home-core':raise ValueError('root-home-core-required')
    release(args.revision)
    rows,paths,before,after=plan()
    report={'revision':args.revision,'before':dict(zip(('caddy','homepage'),map(sha,before))),
            'after':dict(zip(('caddy','homepage'),map(sha,after))), 'applied':False,
            'preserved_maintenance':maintenance_evidence()}
    if not args.apply:
        print(json.dumps(report));return
    if [args.expected_caddy_sha256,args.expected_homepage_sha256]!=list(map(sha,before)):
        raise ValueError('both-reviewed-config-hashes-required')
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    with ExitStack() as stack:
        for name in ('/var/lib/homecompute/deploy.lock','/var/lib/homecompute-docker-maintenance/operation.lock'):
            stream=stack.enter_context(open(name,'a'));fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Build only the two canonical Nix systemd unit derivations. No host
        # generation switch, VM restart, Compose recreation or image pull.
        units={}
        unit_roots=STATE/'unit-roots'/args.revision
        unit_roots.mkdir(mode=0o700,parents=True,exist_ok=True)
        for name in UNITS:
            attr='.#nixosConfigurations.home-core.config.systemd.units."'+name+'".unit'
            output=call(['nix','build','--out-link',str(unit_roots/name),'--print-out-paths',str(ROOT)+attr[1:]],timeout=900).strip()
            target=Path(output)/name
            if not output.startswith('/nix/store/') or not target.is_file():raise ValueError('nix-unit-build-required')
            units[name]=target
        # Candidate validation uses the running container's actual environment
        # and imports; no private environment is exported to the caller.
        call(['docker','exec','-i',CADDY,'caddy','adapt','--config','-','--adapter','caddyfile','--validate'],data=after[0].decode())
        current=plan()
        if current[2]!=before or report['preserved_maintenance']!=maintenance_evidence():
            raise ValueError('baseline-changed-during-build')
        for row,name in zip(rows,(CADDY,HOMEPAGE)):
            if inspect(name)['Id']!=row['Id']:raise ValueError('container-replaced-during-plan')
        backup=STATE/('ingress-'+str(time.time_ns()));backup.mkdir(mode=0o700)
        for label,data in zip(('Caddyfile','services.yaml'),before):
            p=backup/label;p.write_bytes(data);p.chmod(0o600)
        ATTACHED.mkdir(exist_ok=True)
        wants=ATTACHED/'multi-user.target.wants';wants.mkdir(exist_ok=True)
        previous={}
        for name,target in units.items():
            link=ATTACHED/name
            if link.exists() or link.is_symlink():
                if not link.is_symlink() or not str(link.readlink()).startswith('/nix/store/'):
                    raise ValueError('unmanaged-attached-unit-refused')
                previous[str(link)]=str(link.readlink())
            else:previous[str(link)]=None
        changed=[]
        try:
            for name,target in units.items():
                link=ATTACHED/name
                if link.is_symlink():link.unlink()
                link.symlink_to(target)
            link=wants/UNITS[1]
            if not link.is_symlink():link.symlink_to(ATTACHED/UNITS[1])
            call(['systemctl','daemon-reload'])
            call(['systemctl','start',UNITS[1]])
            for path,old,new in zip(paths,before,after):
                if old!=new:in_place(path,old,new);changed.append((path,old,new))
            if before[0]!=after[0]:call(['docker','restart',CADDY],timeout=90)
            if before[1]!=after[1]:call(['docker','restart',HOMEPAGE],timeout=90)
            call(['systemctl','is-active',*UNITS])
            for row,name in zip(rows,(CADDY,HOMEPAGE)):
                live=inspect(name)
                if live['Id']!=row['Id'] or live['Image']!=row['Image'] or network_identity(live)!=network_identity(row):
                    raise ValueError('existing-container-identity-drift')
            if maintenance_evidence()!=report['preserved_maintenance']:raise ValueError('maintenance-marker-drift')
        except Exception:
            for path,old,new in reversed(changed):in_place(path,new,old)
            for name in (CADDY,HOMEPAGE):call(['docker','restart',name],timeout=90)
            # Units are source-backed and harmless behind authentication; retain
            # their receipt for operator inspection rather than hide a partial rollout.
            report.update(applied=False,recovery_required=True,previous_units=previous)
            (backup/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
            raise
        report.update(applied=True,units={k:str(v) for k,v in units.items()},previous_units=previous,backup=str(backup))
        receipt=backup/'receipt.json';receipt.write_text(json.dumps(report,indent=2)+'\n');receipt.chmod(0o600)
        print(json.dumps(report))

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        detail=str(error) if isinstance(error,ValueError) else type(error).__name__
        raise SystemExit('Office ingress held: '+detail+'. Preserve its private receipt before retry.')
