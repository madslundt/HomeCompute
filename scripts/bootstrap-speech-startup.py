#!/usr/bin/env python3
"""Materialize one exact published startup diagnostic release, then execute.

One sudo authentication; fixed public GitHub repo; no packages, sudo policy,
mutable checkout, general release pointers or privileged remote interface.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

REPOSITORY='https://github.com/madslundt/HomeCompute.git'
RELEASES=Path('/srv/homecompute/releases')


def run(arguments):
    result=subprocess.run(arguments,capture_output=True,text=True,timeout=240,
        env={**os.environ,'GIT_TERMINAL_PROMPT':'0','PYTHONDONTWRITEBYTECODE':'1'})
    if result.returncode:raise RuntimeError('published-release-verification-failed')
    return result.stdout.strip()


def protected(directory):
    for path in [*reversed(directory.parents),directory]:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('protected-release-path-required')


def verify(directory,revision):
    protected(directory)
    if (run(['git','-C',str(directory),'rev-parse','HEAD'])!=revision or
            run(['git','-C',str(directory),'status','--porcelain','--untracked-files=all']) or
            run(['git','-C',str(directory),'remote','get-url','origin'])!=REPOSITORY):
        raise ValueError('existing-release-mismatch-nothing-overwritten')
    run(['git','-C',str(directory),'merge-base','--is-ancestor',revision,'origin/master'])


def materialize(revision):
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('exact-published-sha-required')
    RELEASES.mkdir(parents=True,exist_ok=True,mode=0o755);protected(RELEASES)
    target=RELEASES/revision
    if target.exists() or target.is_symlink():verify(target,revision);return target
    temporary=Path(tempfile.mkdtemp(prefix='.speech-startup-',dir=RELEASES))
    try:
        checkout=temporary/'checkout'
        run(['git','clone','--filter=blob:none','--no-checkout','--single-branch','--branch','master',REPOSITORY,str(checkout)])
        run(['git','-C',str(checkout),'checkout','--detach',revision]);verify(checkout,revision)
        checkout.rename(target)
    finally:shutil.rmtree(temporary)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--plan',action='store_true')
    action.add_argument('--plan-sha256')
    parser.add_argument('--result-directory',required=True,type=Path)
    args=parser.parse_args()
    if (os.geteuid()!=0 or socket.gethostname().split('.')[0]!='home-spark' or not sys.flags.isolated):
        raise ValueError('run-python-with-I-and-B-through-sudo-on-home-spark')
    directory=args.result_directory.absolute()
    if (not re.fullmatch(r'/tmp/homecompute-speech-startup\.[A-Za-z0-9]+',str(directory)) or
            directory.is_symlink() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode)!=0o700 or
            directory.stat().st_uid!=int(os.environ.get('SUDO_UID','0'))):
        raise ValueError('fresh-private-operator-staging-required')
    if (directory/'startup-result.json').exists() or (directory/'startup-result.json').is_symlink():
        raise ValueError('fresh-public-result-path-required')
    if args.plan_sha256 is not None and not re.fullmatch('[0-9a-f]{64}',args.plan_sha256):
        raise ValueError('exact-reviewed-plan-required')
    controller=materialize(args.revision)
    if Path(__file__).read_bytes()!=(controller/'scripts/bootstrap-speech-startup.py').read_bytes():
        raise ValueError('staged-bootstrap-differs-from-published-source')
    command=[sys.executable,'-I','-B',str(controller/'scripts/diagnose-speech-startup.py'),
             '--revision',args.revision,'--result-directory',str(directory)]
    command+=['--plan'] if args.plan else ['--plan-sha256',args.plan_sha256]

    os.execv(sys.executable,command)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Startup diagnostic bootstrap refused: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1) from None
