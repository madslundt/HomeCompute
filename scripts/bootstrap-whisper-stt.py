#!/usr/bin/env python3
"""One sudo entry point for a reviewed, published Whisper replacement release.

Stage this public file in a private /tmp/homecompute-whisper-stt.*
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
ACTIONS=('prepare','prepare-runtime-candidate','prepare-cuda-candidate','apply','install','retire','rollback','status','smoke',
         'runtime-candidate','runtime-candidate-rollback','runtime-candidate-status',
         'cuda-candidate','cuda-candidate-rollback','cuda-candidate-status','cuda-candidate-qualify')


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
    temporary=Path(tempfile.mkdtemp(prefix='.whisper-stt-',dir=RELEASES))
    try:
        checkout=temporary/'checkout'
        run(['git','clone','--filter=blob:none','--no-checkout','--single-branch','--branch','master',REPOSITORY,str(checkout)])
        run(['git','-C',str(checkout),'checkout','--detach',revision]);verify(checkout,revision)
        checkout.rename(target)
    finally:shutil.rmtree(temporary)
    return target


def setup_argv(release, args):
    limits = (args.max_audio_seconds, args.max_upload_bytes, args.native_timeout_seconds)
    if any(value is not None for value in limits) and args.action != 'cuda-candidate':
        raise ValueError('request limits are accepted only for cuda-candidate')
    if args.action == 'cuda-candidate-qualify':
        staging = Path(__file__).absolute().parent
        manifest = Path(args.qualification_manifest or '')
        if (not manifest.is_absolute() or manifest.parent != staging or
                manifest.name != 'qualification-manifest.json'):
            raise ValueError('qualification manifest must be in the bootstrap staging directory')
    elif args.qualification_manifest is not None:
        raise ValueError('qualification manifest is accepted only for cuda-candidate-qualify')
    command=[sys.executable,'-I','-B',str(release/'scripts/setup-whisper-stt.py'),
             args.action,'--revision',args.revision]
    if args.qualification_manifest is not None:
        command.extend(('--qualification-manifest',str(args.qualification_manifest)))
    names=('--max-audio-seconds','--max-upload-bytes','--native-timeout-seconds')
    for name,value in zip(names,limits):
        if value is not None:command.extend((name,str(value)))
    return command


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--action',choices=ACTIONS,required=True)
    parser.add_argument('--max-audio-seconds',type=int)
    parser.add_argument('--max-upload-bytes',type=int)
    parser.add_argument('--native-timeout-seconds',type=int)
    parser.add_argument('--qualification-manifest')
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='home-spark' or not sys.flags.isolated:
        raise ValueError('run-python-with-I-and-B-through-sudo-on-home-spark')
    staging=Path(__file__).absolute().parent
    if (not re.fullmatch(r'/tmp/homecompute-whisper-stt\.[A-Za-z0-9]+',str(staging)) or staging.is_symlink() or
            not staging.is_dir() or stat.S_IMODE(staging.stat().st_mode)!=0o700 or
            staging.stat().st_uid!=int(os.environ.get('SUDO_UID','0'))):
        raise ValueError('private-operator-staging-required')
    release=materialize(args.revision)
    if Path(__file__).read_bytes()!=(release/'scripts/bootstrap-whisper-stt.py').read_bytes():
        raise ValueError('staged-bootstrap-differs-from-published-source')
    os.execv(sys.executable,setup_argv(release,args))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Whisper bootstrap refused: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1) from None
