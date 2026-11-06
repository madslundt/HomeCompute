#!/usr/bin/env python3
"""Plan and apply a reviewed assistant briefing policy to its fixed managed path."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from assistant_context_improvements import (AttentionReadSnapshot, ImprovementController,
    ImprovementError, PolicyInstaller, load_controller_config)

@contextmanager
def _controller(config):
    with AttentionReadSnapshot(Path("/srv/state/assistant-context/attention.sqlite3"),Path(config["controller_state"])) as attention:
        yield ImprovementController(attention,source_repo=ROOT,state_dir=Path(config["controller_state"]),
            grant_file=Path(config["standing_grant"]),grant_key_file=Path(config["standing_grant_key"]))

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    initialize=sub.add_parser("initialize",help="create the managed target safely before enabling its Nix selector")
    initialize.add_argument("--revision",required=True)
    plan=sub.add_parser("plan",help="bind a reviewed source revision to the installed policy preimage")
    plan.add_argument("--revision",required=True); plan.add_argument("--candidate-id",required=True)
    plan.add_argument("--expected-installed-sha256",required=True)
    apply=sub.add_parser("apply",help="apply a previously reviewed hash-bound plan")
    apply.add_argument("--transaction-id",required=True); apply.add_argument("--plan-sha256",required=True)
    rollback=sub.add_parser("rollback",help="restore a prior policy preimage if the installed hash still matches")
    rollback.add_argument("--transaction-id",required=True); rollback.add_argument("--expected-installed-sha256",required=True)
    args=parser.parse_args(argv)
    try:
        if platform.system()!="Linux" or os.geteuid()!=0:
            raise ImprovementError("policy lifecycle requires root on Linux home-core")
        config=load_controller_config(ROOT/"config/assistant-improvements.json")
        with _controller(config) as controller:
            installer=PolicyInstaller(controller,repo=ROOT,state_dir=Path(config["controller_state"]))
            if args.command=="initialize": result=installer.plan_initialize(args.revision)
            elif args.command=="plan": result=installer.plan_candidate(args.revision,args.candidate_id,args.expected_installed_sha256)
            elif args.command=="apply": result=installer.apply(args.transaction_id,args.plan_sha256)
            else: result=installer.rollback(args.transaction_id,args.expected_installed_sha256)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(",",":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status":"held","reason":type(exc).__name__},separators=(",",":")),file=sys.stderr)
        return 2

if __name__=="__main__": raise SystemExit(main())
