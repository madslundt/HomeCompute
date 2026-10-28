#!/usr/bin/env python3
"""Run the pinned qualification pipeline for one explicitly reviewed plan hash.

Only the approval input is replaced. The pinned release owns corpus validation,
fresh measurement, model lifecycle, signals, intake restoration and rollback.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
RELEASES=Path('/srv/homecompute/releases')
REPOSITORY='https://github.com/madslundt/HomeCompute.git'
QUALIFICATION_REVISION='9c63ffab299e7325419a4515cb8acb3b4d5b7dce'
QUALIFICATION_ROOT=RELEASES/QUALIFICATION_REVISION
APPROVED_PLAN='0c36bc423531730ae23bec789c2c06896376f0500028b4c6309dbed0cb75d0dd'


class ApprovedPlanMismatch(ValueError):pass


def run(arguments):
    result=subprocess.run(arguments,capture_output=True,text=True,timeout=60)
    if result.returncode:raise ValueError('published-source-verification-failed')
    return result.stdout.strip()


def protected(path):
    for item in [*reversed(path.parents),path]:
        info=item.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('protected-release-path-required')


def verify_release(directory: Path, revision: str):
    if not re.fullmatch('[0-9a-f]{40}',revision) or directory!=RELEASES/revision:
        raise ValueError('exact-published-release-path-required')
    protected(directory)
    if (run(['git','-C',str(directory),'rev-parse','HEAD'])!=revision or
            run(['git','-C',str(directory),'status','--porcelain','--untracked-files=all']) or
            run(['git','-C',str(directory),'remote','get-url','origin'])!=REPOSITORY):
        raise ValueError('immutable-release-or-origin-mismatch')
    run(['git','-C',str(directory),'merge-base','--is-ancestor',revision,'origin/master'])


def load_pinned():
    # The controller is launched with -I. Its only added import directory is
    # this verified old release; no module comes from the staging directory or
    # the controller release's potentially newer speech implementation.
    scripts=QUALIFICATION_ROOT/'scripts'
    existing=sys.modules.get('speech_capacity')
    if existing is not None and Path(existing.__file__).resolve()!=scripts/'speech_capacity.py':
        raise ValueError('qualification-library-was-imported-from-another-release')
    sys.path.insert(0,str(scripts))
    spec=importlib.util.spec_from_file_location('approved_pinned_spark_rollout',scripts/'spark-rollout.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    imported=sys.modules['speech_capacity']
    if module.ROOT!=QUALIFICATION_ROOT or Path(imported.__file__).resolve()!=scripts/'speech_capacity.py':
        raise ValueError('qualification-root-or-import-identity-mismatch')
    return module


def install_approved_trial(module, approved: str):
    if approved!=APPROVED_PLAN:raise ApprovedPlanMismatch('The supplied hash is not this reviewed plan')
    original=module.trial
    def exact(plan):
        if not isinstance(plan,dict) or plan.get('plan_sha256')!=approved:
            raise ApprovedPlanMismatch('Fresh plan differs from the explicitly reviewed hash')
        return True
    def measure(label,directory):
        if label=='baseline':
            # Pinned trial writes this fresh root-private receipt immediately
            # after its own preflight, before invoking the measure callback.
            path=directory/'preflight-plan.json';info=path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                    info.st_mode&0o077 or info.st_size>65536):
                raise ValueError('private-fresh-preflight-plan-required')
            exact(json.loads(path.read_bytes()))
        return module.benchmark(label,directory)
    def trial(directory):
        return original(directory,confirmer=exact,measure=measure)
    module.trial=trial


def execute_pinned(module,archive: Path):
    previous=sys.argv
    try:
        sys.argv=[str(QUALIFICATION_ROOT/'scripts/spark-rollout.py'),'--revision',QUALIFICATION_REVISION,
                  '--corpus-archive',str(archive)]
        return module.main()
    finally:sys.argv=previous


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller-revision',required=True)
    parser.add_argument('--approve-plan-sha256',required=True)
    parser.add_argument('--corpus-archive',required=True,type=Path)
    args=parser.parse_args()
    if (os.geteuid()!=0 or socket.gethostname().split('.')[0]!='home-spark' or not sys.flags.isolated):
        raise ValueError('run-isolated-published-controller-with-sudo-on-home-spark')
    if args.approve_plan_sha256!=APPROVED_PLAN:raise ApprovedPlanMismatch('Explicit approval must match this reviewed plan')
    verify_release(ROOT,args.controller_revision);verify_release(QUALIFICATION_ROOT,QUALIFICATION_REVISION)
    # Old helper subprocesses do not inherit the interpreter's -I switch.
    # Keep their import/interactive behavior independent of ambient Python env.
    for key in list(os.environ):
        if key.startswith('PYTHON'):os.environ.pop(key)
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    module=load_pinned();install_approved_trial(module,args.approve_plan_sha256)
    print(json.dumps({'controller_revision':args.controller_revision,'qualification_revision':QUALIFICATION_REVISION,
        'approved_plan_sha256':APPROVED_PLAN,'accepted':False,'stdin_approval_required':False}),flush=True)
    execute_pinned(module,args.corpus_archive)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'state':'approved-plan-controller-failed','error_category':type(error).__name__,'accepted':False}),file=sys.stderr)
        raise SystemExit(1) from None
