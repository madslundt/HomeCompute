#!/usr/bin/env python3
"""Credential-free pinned Codex sandbox diagnostics on home-core.

Default: disposable network=none container, unchanged Docker security profile.
--native-host-probe: additionally extract the same binaries to a disposable
directory and run the normal sandbox as an unused numeric UID without caps.
This diagnostic does not qualify a host-direct production worker or change
Docker, kernel, AppArmor, services, credentials, or repository policy.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess

IMAGE = "sha256:6c8c176813e15c6eb3f41507b44b43a991d8908a83588f44fc501057ae647d88"
REMOTE = r'''
import ctypes,json,os,pathlib,pwd,shutil,subprocess,tempfile

IMAGE = "sha256:6c8c176813e15c6eb3f41507b44b43a991d8908a83588f44fc501057ae647d88"
def run(argv, **options):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=40, **options)
    return {"exit_code":result.returncode,"stdout":result.stdout.strip(),"stderr":result.stderr.strip()}

record = {"kernel":os.uname().release,"docker":run(["docker","version","--format","{{.Server.Version}}"]),
          "image":run(["docker","image","inspect",IMAGE,"--format","{{.Id}}"]),
          "user_namespace_limit":int(pathlib.Path('/proc/sys/user/max_user_namespaces').read_text()),
          "paid_api_calls":0,"github_writes":0,"production_changed":False}
if record["image"]["exit_code"] or record["image"]["stdout"] != IMAGE:
    raise RuntimeError("pinned previously built validation image unavailable")
program = r"""
import ctypes,json,os,pathlib,subprocess
os.setgroups([]);os.setgid(20000);os.setuid(20000)
assert os.getuid()==20000 and os.getgroups()==[]
assert not any(key in os.environ for key in ('CODEX_API_KEY','OPENAI_API_KEY'))
c=ctypes.CDLL(None,use_errno=True)
p=subprocess.run(['python3','-c','import ctypes,sys;c=ctypes.CDLL(None,use_errno=True);r=c.unshare(0x10000000);print(r,ctypes.get_errno());sys.exit(r!=0)'],capture_output=True,text=True)
print(json.dumps({'user_namespace_probe':{'exit_code':p.returncode,'output':p.stdout.strip()}}))
command=['codex','-a','never','--sandbox','workspace-write','-c','sandbox_mode="workspace-write"','sandbox','--','python3','-c','from pathlib import Path;p=Path("synthetic-ready");p.write_text("synthetic");assert p.read_text()=="synthetic";p.unlink()']
r=subprocess.run(command,capture_output=True,text=True,timeout=20)
print(json.dumps({'codex_workspace_write':{'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}}))
"""
record["default_docker"] = run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
    '--cap-add','SETUID','--cap-add','SETGID','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','KILL',
    '--security-opt','no-new-privileges:true','--user','0:0','--cpus','0.5','--memory','256m',
    '--memory-swap','256m','--pids-limit','64','--tmpfs','/work:rw,nosuid,nodev,size=16m,uid=20000,gid=20000,mode=0700',
    '--tmpfs','/tmp:rw,nosuid,nodev,size=16m,uid=20000,gid=20000,mode=0700','-w','/work','-e','HOME=/work',
    '--entrypoint','python3',IMAGE,'-c',program])
if NATIVE:
    uid=42023
    try: pwd.getpwuid(uid)
    except KeyError: pass
    else: raise RuntimeError('synthetic UID already belongs to an account')
    for path in pathlib.Path('/proc').iterdir():
        if path.name.isdigit():
            try: line=next(line for line in (path/'status').read_text().splitlines() if line.startswith('Uid:'))
            except (FileNotFoundError,PermissionError,StopIteration): continue
            if uid in [int(n) for n in line.split()[1:]]: raise RuntimeError('synthetic UID already has processes')
    with tempfile.TemporaryDirectory(prefix='hc-codex-isolation-') as temporary:
        root=pathlib.Path(temporary);root.chmod(0o711)
        created=run(['docker','create','--network','none','--read-only','--cap-drop','ALL',
                     '--security-opt','no-new-privileges:true','--entrypoint','/bin/true',IMAGE])
        if created['exit_code']: raise RuntimeError('disposable extraction container failed')
        container=created['stdout']
        try:
            source='/opt/worker/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl'
            copied=run(['docker','cp',container+':'+source,str(root/'vendor')])
            if copied['exit_code']: raise RuntimeError('pinned binary extraction failed')
        finally: subprocess.run(['docker','rm','-f',container],check=True,stdout=subprocess.DEVNULL,timeout=10)
        for path in (root/'vendor').rglob('*'):
            if path.is_dir(): path.chmod(0o755)
            elif path.name in ('codex','bwrap'): path.chmod(0o555)
            else: path.chmod(0o444)
        work=root/'workspace';work.mkdir(mode=0o700);os.chown(work,uid,uid)
        for name in ('home','tmp'):
            path=root/name;path.mkdir(mode=0o700);os.chown(path,uid,uid)
        outside=root/'outside-synthetic';outside.write_text('synthetic');outside.chmod(0o666)
        private=root/'private-synthetic';private.write_text('synthetic');private.chmod(0o400)
        git=work/'.git';git.mkdir();os.chown(git,uid,uid)
        protected=git/'config';protected.write_text('synthetic');os.chown(protected,uid,uid)
        probe=r"""
import json,os,pathlib,socket
assert os.getuid()==42023 and os.getgroups()==[]
assert not any(key in os.environ for key in ('CODEX_API_KEY','OPENAI_API_KEY'))
pathlib.Path('synthetic-ready').write_text('synthetic')
assert pathlib.Path('synthetic-ready').read_text()=='synthetic'
checks={'workspace_write':'pass'}
for label,path in [('outside_write',OUTSIDE),('protected_git_write','.git/config')]:
    try: pathlib.Path(path).write_text('changed')
    except OSError: checks[label]='denied'
    else: raise AssertionError(label+' unexpectedly allowed')
try: pathlib.Path(PRIVATE).read_text()
except OSError: checks['private_sentinel_read']='denied'
else: raise AssertionError('private sentinel read allowed')
try: socket.socket(socket.AF_INET,socket.SOCK_STREAM)
except OSError: checks['network_socket']='denied'
else: raise AssertionError('network socket allowed')
print(json.dumps(checks))
""".replace('OUTSIDE',repr(str(outside))).replace('PRIVATE',repr(str(private)))
        env={'PATH':'/run/current-system/sw/bin','HOME':str(root/'home'),'CODEX_HOME':str(root/'home'),
             'TMPDIR':str(root/'tmp'),'LANG':'C.UTF-8'}
        argv=['timeout','30','setpriv','--reuid='+str(uid),'--regid='+str(uid),'--clear-groups',
              '--bounding-set=-all','--inh-caps=-all','--ambient-caps=-all','--no-new-privs',
              str(root/'vendor/bin/codex'),'-a','never','--sandbox','workspace-write','-c','sandbox_mode="workspace-write"',
              '-c','sandbox_workspace_write.exclude_slash_tmp=true',
              'sandbox','--','python3','-c',probe]
        record['native_numeric_uid']=run(argv,cwd=work,env=env)
        record['native_numeric_uid']['configuration']={'sandbox_mode':'workspace-write','exclude_slash_tmp':True,
                                                     'private_tmpdir':True,'approval_policy':'never'}
        record['native_numeric_uid']['outer_isolation_qualified']=False
        record['native_numeric_uid']['scope']='disposable synthetic kernel capability proof; not production executor'
    record['native_tempdir_removed']=not root.exists()
print(json.dumps(record))
'''


def verify(native: bool) -> dict[str, object]:
    payload = base64.b64encode(("NATIVE=" + repr(native) + "\n" + REMOTE).encode()).decode()
    command = "sudo -n python3 -c " + shlex.quote(
        "import base64;exec(base64.b64decode(" + repr(payload) + "))")
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=10", "home-core", command],
                            capture_output=True, text=True, timeout=100)
    if result.returncode:
        raise RuntimeError("credential-free isolation diagnostic failed: " + result.stderr.strip())
    record = json.loads(result.stdout)
    docker = record["default_docker"]
    if docker["exit_code"] == 0:
        docker["probes"] = [json.loads(line) for line in docker["stdout"].splitlines()]
        del docker["stdout"]
    record["diagnostic_complete"] = (docker["exit_code"] == 0 and (not native or
                                     record["native_numeric_uid"]["exit_code"] == 0))
    record["validated_at"] = datetime.now(timezone.utc).isoformat()
    record["production_worker_enabled"] = False
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-host-probe", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    record = verify(args.native_host_probe)
    encoded = json.dumps(record, indent=2) + "\n"
    if args.report:
        args.report.write_text(encoded)
    print(encoded, end="")
    raise SystemExit(0 if record["diagnostic_complete"] else 1)


if __name__ == "__main__":
    main()
