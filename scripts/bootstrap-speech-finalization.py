#!/usr/bin/env python3
"""One sudo entry point for a reviewed, published speech finalization release.

Stage this public file in a private /tmp/homecompute-speech-finalization.*
directory. It imports no staging modules, installs no packages, changes no sudo
policy and never replaces the mutable checkout or general release pointers.
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
QUALIFICATION_REVISION='9c63ffab299e7325419a4515cb8acb3b4d5b7dce'


def run(arguments):
    result=subprocess.run(arguments,capture_output=True,text=True,timeout=240,
                          env={**os.environ,'GIT_TERMINAL_PROMPT':'0','PYTHONDONTWRITEBYTECODE':'1'})
    if result.returncode:raise RuntimeError('published-release-verification-failed')
    return result.stdout.strip()


def protected(path):
    for item in [*reversed(path.parents),path]:
        info=item.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('root-owned-release-path-required')


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
    temporary=Path(tempfile.mkdtemp(prefix='.speech-finalization-',dir=RELEASES))
    try:
        checkout=temporary/'checkout'
        run(['git','clone','--filter=blob:none','--no-checkout','--single-branch','--branch','master',REPOSITORY,str(checkout)])
        run(['git','-C',str(checkout),'checkout','--detach',revision]);verify(checkout,revision)
        checkout.rename(target)
    finally:shutil.rmtree(temporary)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True);parser.add_argument('--trial',required=True)
    parser.add_argument('--qualification-sha256',required=True);parser.add_argument('--recover',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='home-spark' or not sys.flags.isolated:
        raise ValueError('run-python-with-I-and-B-through-sudo-on-home-spark')
    staging=Path(__file__).absolute().parent
    if (not re.fullmatch(r'/tmp/homecompute-speech-finalization\.[A-Za-z0-9]+',str(staging)) or staging.is_symlink() or
            not staging.is_dir() or stat.S_IMODE(staging.stat().st_mode)!=0o700 or
            staging.stat().st_uid!=int(os.environ.get('SUDO_UID','0'))):raise ValueError('private-operator-staging-required')
    if not re.fullmatch(r'trial-[A-Za-z0-9_-]+',args.trial) or not re.fullmatch('[0-9a-f]{64}',args.qualification_sha256):
        raise ValueError('exact-reviewed-trial-and-result-hash-required')
    release=materialize(args.revision);materialize(QUALIFICATION_REVISION)
    if Path(__file__).read_bytes()!=(release/'scripts/bootstrap-speech-finalization.py').read_bytes():
        raise ValueError('staged-bootstrap-differs-from-published-source')
    command=[sys.executable,'-I','-B',str(release/'scripts/finalize-speech-capacity.py'),'--revision',args.revision,
             '--trial',args.trial,'--qualification-sha256',args.qualification_sha256,'--staging-directory',str(staging)]
    if args.recover:command+=['--recover']
    os.execv(sys.executable,command)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Finalization bootstrap refused: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1) from None
