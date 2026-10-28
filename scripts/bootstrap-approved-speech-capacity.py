#!/usr/bin/env python3
"""Materialize a reviewed controller and exact old qualification, then execute.

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
QUALIFICATION_REVISION='9c63ffab299e7325419a4515cb8acb3b4d5b7dce'
APPROVED_PLAN='0c36bc423531730ae23bec789c2c06896376f0500028b4c6309dbed0cb75d0dd'


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
    temporary=Path(tempfile.mkdtemp(prefix='.approved-speech-',dir=RELEASES))
    try:
        checkout=temporary/'checkout'
        run(['git','clone','--filter=blob:none','--no-checkout','--single-branch','--branch','master',REPOSITORY,str(checkout)])
        run(['git','-C',str(checkout),'checkout','--detach',revision]);verify(checkout,revision)
        checkout.rename(target)
    finally:shutil.rmtree(temporary)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True);parser.add_argument('--approve-plan-sha256',required=True)
    parser.add_argument('--corpus-archive',required=True,type=Path)
    args=parser.parse_args()
    if (os.geteuid()!=0 or socket.gethostname().split('.')[0]!='home-spark' or not sys.flags.isolated):
        raise ValueError('run-python-with-I-and-B-through-sudo-on-home-spark')
    if args.approve_plan_sha256!=APPROVED_PLAN:raise ValueError('only-the-explicitly-reviewed-plan-is-admitted')
    archive=args.corpus_archive.absolute();directory=archive.parent
    if (not re.fullmatch(r'/tmp/homecompute-spark-rollout\.[A-Za-z0-9]+/corpus.tar',str(archive)) or
            directory.is_symlink() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode)!=0o700 or
            directory.stat().st_uid!=int(os.environ.get('SUDO_UID','0')) or archive.is_symlink() or not archive.is_file()):
        raise ValueError('fresh-private-public-corpus-staging-required')
    if (directory/'rollout-result.json').exists() or (directory/'rollout-result.json').is_symlink():
        raise ValueError('fresh-public-result-path-required')
    controller=materialize(args.revision);materialize(QUALIFICATION_REVISION)
    if Path(__file__).read_bytes()!=(controller/'scripts/bootstrap-approved-speech-capacity.py').read_bytes():
        raise ValueError('staged-bootstrap-differs-from-published-source')
    command=[sys.executable,'-I','-B',str(controller/'scripts/run-approved-speech-capacity.py'),
             '--controller-revision',args.revision,'--approve-plan-sha256',args.approve_plan_sha256,
             '--corpus-archive',str(archive)]
    os.execv(sys.executable,command)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Approved-plan bootstrap refused: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1) from None
