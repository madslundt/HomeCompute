#!/usr/bin/env python3
"""Run the restricted HomeCompute assistant improvement controller."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from assistant_attention import Scope
from assistant_context_improvements import (AttentionReadSnapshot, ImprovementController,
    ImprovementError, load_controller_config)

OWNER=Scope("owner","personal","private")

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    sweep=sub.add_parser("sweep",help="independently test candidates, reconcile reviewed PRs, and optionally apply")
    sweep.add_argument("--enable-controller",action="store_true",help="explicit opt-in supplied only by the restricted systemd unit")
    sweep.add_argument("--apply-reviewed",action="store_true",help="also apply only reconciled origin/master policy under guarded lifecycle")
    status=sub.add_parser("status",help="read sanitized controller state")
    status.add_argument("candidate_id")
    prepare=sub.add_parser("prepare",help="produce a deterministic config-only review patch")
    prepare.add_argument("candidate_id"); prepare.add_argument("expected_source_sha256")
    for name in ("test","canary","promote","publish","reconcile"):
        action=sub.add_parser(name); action.add_argument("candidate_id")
    args=parser.parse_args(argv)
    try:
        if args.command=="sweep" and args.apply_reviewed and not args.enable_controller:
            raise ImprovementError("--apply-reviewed requires explicit controller opt-in")
        config=load_controller_config(ROOT/"config/assistant-improvements.json")
        with AttentionReadSnapshot(Path("/srv/state/assistant-context/attention.sqlite3"),Path(config["controller_state"])) as attention:
            if platform.system()!="Linux" or os.geteuid()!=0:
                raise ImprovementError("controller CLI must run as its restricted Linux service identity")
            c=ImprovementController(attention,source_repo=ROOT,state_dir=Path(config["controller_state"]),
                grant_file=Path(config["standing_grant"]),grant_key_file=Path(config["standing_grant_key"]),
                enabled=bool(args.command=="sweep" and args.enable_controller))
            if args.command=="sweep": result=c.sweep(OWNER,apply_reviewed=args.apply_reviewed)
            elif args.command=="status": result=c.get(OWNER,args.candidate_id)
            elif args.command=="prepare": result=c.prepare(OWNER,args.candidate_id,args.expected_source_sha256,str(uuid.uuid4()))
            else: result=getattr(c,args.command)(OWNER,args.candidate_id,str(uuid.uuid4()))
        print(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(",",":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status":"held","reason":type(exc).__name__},separators=(",",":")),file=sys.stderr)
        return 2

if __name__=="__main__": raise SystemExit(main())
