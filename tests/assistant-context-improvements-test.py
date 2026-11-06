from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from assistant_attention import AttentionLedger, Scope
from assistant_context_improvements import (ImprovementController, ImprovementError, GitPublisher,
    AttentionReadSnapshot, IndependentEvaluator, PolicyInstaller, SystemdPolicyOps, apply_candidate_diff, heldout_replay,
    load_controller_config, sha, canonical)

NOW=datetime(2026,11,6,9,0,tzinfo=timezone.utc)
OWNER=Scope("owner","personal","private")

class FakeAttention:
    def __init__(self): self.rows={}
    def add(self,candidate_id,allowed_class="briefing-format",proposed_diff=None):
        self.rows[candidate_id]={"candidate_id":candidate_id,"state":"proposed","revision":1,
            "allowed_class":allowed_class,"proposed_diff":proposed_diff or {"max_bullets":5},
            "source_refs":["PRIVATE_FIXTURE_SHOULD_NOT_LEAK"]}
    def get_candidate(self,*,scope,candidate_id):
        if (scope.principal_scope,scope.data_domain,scope.visibility)!=("owner","personal","private"): return None
        return self.rows.get(candidate_id)
    def list_candidates(self,*,scope,limit): return list(self.rows.values())[:limit]
    def transition_candidate(self,*,scope,candidate_id,transition,expected_revision,request_id,now):
        row=self.rows[candidate_id]
        if row["revision"]!=expected_revision or transition!="rejected": raise ValueError("candidate state conflict")
        row["state"]="rejected"; row["revision"]+=1; return row

class FixedEvaluator:
    def evaluate(self,revision,before,after,allowed_class):
        replay=heldout_replay(before,after,allowed_class)
        return {"evaluator":"fake-independent-suite","trusted_revision":revision,"passed":replay["passed"],
            "private_data_used":False,"commands":[{"returncode":0}],"replay":replay}
    def canary(self,revision,before,after,allowed_class,rounds):
        receipts=[heldout_replay(before,after,allowed_class,round_number=i+1) for i in range(rounds)]
        return {"evaluator":"fake-canary","passed":rounds==3 and all(x["passed"] for x in receipts),"rounds":receipts}

class FixedReconciler:
    def __init__(self,revision): self.revision=revision
    def reconcile(self,branch,expected_head):
        return {"status":"reconciled","merged_revision":self.revision}

class FakePolicyOps:
    def __init__(self,selector="",health=(True,)):
        self.selector=selector; self.running=True; self.health_values=list(health); self.calls=[]
    def exec_start(self): return self.selector
    def active(self): return self.running
    def stop(self): self.calls.append("stop"); self.running=False
    def start(self): self.calls.append("start"); self.running=True
    def health(self): self.calls.append("health"); return self.health_values.pop(0) if self.health_values else True

class SimulatedCrash(BaseException): pass

class ImprovementsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.remote=self.root/"homecompute.git"; self.repo=self.root/"repo"
        self._git(["init","--bare","--initial-branch=master",str(self.remote)])
        self._git(["init","--initial-branch=master",str(self.repo)])
        (self.repo/"config").mkdir(); (self.repo/"tests").mkdir(); (self.repo/"scripts").mkdir()
        shutil.copy(ROOT/"config/assistant-briefings.json",self.repo/"config/assistant-briefings.json")
        (self.repo/"tests/assistant-context-briefings-test.py").write_text("raise SystemExit(0)\n")
        (self.repo/"scripts/validate-repository.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.repo/"scripts/validate-repository.sh").chmod(0o755)
        self._git(["-C",str(self.repo),"config","user.name","test"]); self._git(["-C",str(self.repo),"config","user.email","test@example.invalid"])
        self._git(["-C",str(self.repo),"add","."]); self._git(["-C",str(self.repo),"commit","-m","fixture source"])
        self._git(["-C",str(self.repo),"remote","add","origin",str(self.remote)])
        self._git(["-C",str(self.repo),"push","-u","origin","master"])
        self.revision=self._git(["-C",str(self.repo),"rev-parse","origin/master"])
        self.before=(self.repo/"config/assistant-briefings.json").read_bytes()
        self.attention=FakeAttention(); self.candidate_id="a"*32; self.attention.add(self.candidate_id)
        self.state=self.root/"controller"; self.state.mkdir(mode=0o700)
        self.grant_file=self.state/"standing-grant.json"; self.grant_key=self.state/"standing-grant.key"
        self.grant_key.write_bytes(b"k"*32); self.grant_key.chmod(0o600)
        self._grant(["briefing-format","attention-threshold"])
        self.controller=ImprovementController(self.attention,source_repo=self.repo,state_dir=self.state,
            grant_file=self.grant_file,grant_key_file=self.grant_key,evaluator=FixedEvaluator(),
            publisher=GitPublisher(self.repo,remote=str(self.remote),test_remote=True),
            reconciler=FixedReconciler("c"*40),clock=lambda:NOW,test_mode=True,status_path=self.state/"status.json")

    def tearDown(self): self.temp.cleanup()

    @staticmethod
    def _git(args):
        result=subprocess.run(["git",*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode: raise AssertionError(result.stderr.decode())
        return result.stdout.decode().strip()

    def _grant(self,classes,max_candidates=10):
        value={"schema_version":1,"grant_id":"d"*32,"principal_scope":"owner","data_domain":"personal","visibility":"private",
            "allowed_classes":classes,"issued_at":"2026-11-06T00:00:00Z","expires_at":"2026-12-01T00:00:00Z","max_candidates":max_candidates}
        value["signature"]=hmac.new(b"k"*32,canonical(value),hashlib.sha256).hexdigest()
        self.grant_file.write_text(json.dumps(value)); self.grant_file.chmod(0o600)

    def _prepare(self):
        return self.controller.prepare(OWNER,self.candidate_id,sha(self.before),str(uuid.uuid4()))

    def _advance_to_canary(self):
        self._prepare(); self.controller.test(OWNER,self.candidate_id,str(uuid.uuid4()))
        return self.controller.canary(OWNER,self.candidate_id,str(uuid.uuid4()))

    def test_only_allowlisted_fields_change_and_patch_contains_no_private_refs(self):
        prepared=self._prepare()
        self.assertEqual(prepared["source_file"],"config/assistant-briefings.json")
        self.assertEqual(prepared["before_sha256"],sha(self.before))
        self.assertNotIn("PRIVATE_FIXTURE_SHOULD_NOT_LEAK",json.dumps(prepared))
        self.assertIn('"max_bullets": 5',prepared["patch"])
        before=json.loads(self.before); after=json.loads(self.controller._get(OWNER,self.candidate_id)["after_json"])
        before["formatting"]["max_bullets"]=5
        self.assertEqual(before,after)
        projection=self.controller.read_status_projection(self.state/"status.json",strict=False)
        self.assertEqual(set(projection["candidates"][0]),{"candidate_id","allowed_class","state","revision","source_revision","before_sha","after_sha","branch","head_sha","merged_revision","published","applied","grant_id","updated_at"})
        self.assertNotIn("PRIVATE_FIXTURE_SHOULD_NOT_LEAK",json.dumps(projection))

    def test_harmful_extra_and_source_link_disabling_diffs_are_refused(self):
        for candidate in ({"timezone":"UTC"},{"max_bullets":5,"daily_discretionary_notification_limit":20}):
            with self.assertRaises(ImprovementError): apply_candidate_diff(self.before,"briefing-format",candidate)
        with self.assertRaises(ImprovementError): apply_candidate_diff(self.before,"briefing-format",{"include_source_links":False})
        with self.assertRaises(ImprovementError): apply_candidate_diff(self.before,"attention-threshold",{"minimum_relevance":0.1})
        with self.assertRaises(ImprovementError): apply_candidate_diff(self.before,"attention-threshold",{"unknown":1})

    def test_heldout_quality_gate_rejects_empty_or_unreadable_briefing(self):
        narrow,_=apply_candidate_diff(self.before,"briefing-format",{"max_bullets":2})
        lost=heldout_replay(self.before,narrow,"briefing-format")
        self.assertFalse(lost["passed"])
        self.assertLess(len(lost["metrics"]["candidate_cited_sources"]),len(lost["metrics"]["baseline_cited_sources"]))
        short,_=apply_candidate_diff(self.before,"briefing-format",{"max_chars":100})
        self.assertFalse(heldout_replay(self.before,short,"briefing-format")["passed"])
        suppress_all,_=apply_candidate_diff(self.before,"attention-threshold",{"minimum_repeat_evidence":10})
        self.assertFalse(heldout_replay(self.before,suppress_all,"attention-threshold")["passed"])

    def test_optimistic_source_hash_and_duplicate_concurrent_request(self):
        with self.assertRaises(ImprovementError): self.controller.prepare(OWNER,self.candidate_id,"0"*64,str(uuid.uuid4()))
        request_id=str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.controller.prepare(OWNER,self.candidate_id,sha(self.before),request_id),range(8)))
        self.assertEqual({r["after_sha256"] for r in results},{results[0]["after_sha256"]})
        self.assertEqual(results[0],results[-1])
        self.assertEqual(self.controller.prepare(OWNER,self.candidate_id,sha(self.before),request_id),results[0])

    def test_independent_replay_failure_rejects_candidate(self):
        self.attention.add("b"*32,proposed_diff={"max_bullets":2})
        self.controller.prepare(OWNER,"b"*32,sha(self.before),str(uuid.uuid4()))
        with self.assertRaises(ImprovementError): self.controller.test(OWNER,"b"*32,str(uuid.uuid4()))
        self.assertEqual(self.attention.rows["b"*32]["state"],"proposed")
        self.assertEqual(self.controller.get(OWNER,"b"*32)["state"],"rejected")

    def test_trusted_evaluator_runs_fixed_commands_in_an_archived_temp_checkout(self):
        after,_=apply_candidate_diff(self.before,"briefing-format",{"max_bullets":5})
        receipt=IndependentEvaluator(self.repo).evaluate(self.revision,self.before,after,"briefing-format")
        self.assertTrue(receipt["passed"])
        self.assertEqual([row["returncode"] for row in receipt["commands"]],[0,0])
        self.assertEqual(receipt["changed_file"],"config/assistant-briefings.json")
        self.assertFalse(receipt["private_data_used"])

    def test_forged_candidate_report_cannot_skip_trusted_evaluator_or_canary(self):
        self._prepare()
        with self.assertRaises(ImprovementError): self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))
        self.controller.test(OWNER,self.candidate_id,str(uuid.uuid4()))
        row=self.controller._get(OWNER,self.candidate_id)
        with self.assertRaises(ImprovementError): self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))
        self.controller.canary(OWNER,self.candidate_id,str(uuid.uuid4()))
        with sqlite3_connect(self.controller.db_path) as db:
            db.execute("UPDATE candidates SET canary_receipt_json=? WHERE candidate_id=?",(json.dumps({"receipt":{"passed":True},"controller_hmac":"0"*64}),self.candidate_id))
        with self.assertRaises(ImprovementError): self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))

    def test_one_standing_grant_approves_multiple_candidates_without_per_change_prompt(self):
        self.attention.add("b"*32,proposed_diff={"max_chars":600})
        for cid in (self.candidate_id,"b"*32):
            self.controller.prepare(OWNER,cid,sha(self.before),str(uuid.uuid4()))
            self.controller.test(OWNER,cid,str(uuid.uuid4()))
            self.controller.canary(OWNER,cid,str(uuid.uuid4()))
            result=self.controller.promote(OWNER,cid,str(uuid.uuid4()))
            self.assertEqual(result["state"],"approved")
            self.assertEqual(result["grant_id"],"d"*32)

    def test_standing_grant_quota_is_atomic_across_candidates(self):
        self._grant(["briefing-format"],max_candidates=1)
        self.attention.add("b"*32,proposed_diff={"max_chars":600})
        for cid in (self.candidate_id,"b"*32):
            self.controller.prepare(OWNER,cid,sha(self.before),str(uuid.uuid4()))
            self.controller.test(OWNER,cid,str(uuid.uuid4())); self.controller.canary(OWNER,cid,str(uuid.uuid4()))
        self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))
        with self.assertRaises(ImprovementError): self.controller.promote(OWNER,"b"*32,str(uuid.uuid4()))

    def test_local_bare_publication_is_config_only_and_awaits_protected_review(self):
        self._advance_to_canary(); self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))
        publisher=self.controller.publisher; publish=publisher.publish
        def push_then_lose_response(*args):
            publish(*args)
            raise OSError("simulated lost publisher response")
        publisher.publish=push_then_lose_response
        with self.assertRaises(OSError): self.controller.publish(OWNER,self.candidate_id,str(uuid.uuid4()))
        self.assertEqual(self.controller.get(OWNER,self.candidate_id)["state"],"publishing")
        publisher.publish=publish
        published=self.controller.publish(OWNER,self.candidate_id,str(uuid.uuid4()))
        self.assertEqual(published["state"],"published")
        branch="codex/assistant-improvement-"+self.candidate_id
        names=self._git(["--git-dir",str(self.remote),"diff-tree","--no-commit-id","--name-only","-r",published["head_sha"]]).splitlines()
        self.assertEqual(names,["config/assistant-briefings.json"])
        self.assertEqual(published["published"],1)
        with self.assertRaises(ImprovementError): self.controller.mark_applied(OWNER,self.candidate_id,published["head_sha"],str(uuid.uuid4()))
        self._git(["-C",str(self.repo),"fetch","origin",branch])
        self._git(["-C",str(self.repo),"merge","--no-edit","--no-ff","FETCH_HEAD","-m","reviewed candidate merge"])
        self._git(["-C",str(self.repo),"push","origin","master"])
        self._git(["-C",str(self.repo),"fetch","origin","master"])
        merged=self._git(["-C",str(self.repo),"rev-parse","origin/master"])
        self.controller.reconciler.revision=merged
        reconciled=self.controller.reconcile(OWNER,self.candidate_id,str(uuid.uuid4()))
        self.assertEqual(reconciled["state"],"reconciled")
        applied=self.controller.mark_applied(OWNER,self.candidate_id,merged,str(uuid.uuid4()))
        self.assertEqual(applied["state"],"applied")

    def test_enabled_sweep_drives_candidate_and_reconciles_reviewed_merge(self):
        self.controller.enabled=True
        queued=self.controller.sweep(OWNER)
        self.assertEqual(queued[0]["state"],"published")
        branch=queued[0]["branch"]
        self._git(["-C",str(self.repo),"fetch","origin",branch])
        self._git(["-C",str(self.repo),"merge","--no-edit","--no-ff","FETCH_HEAD","-m","reviewed candidate merge"])
        self._git(["-C",str(self.repo),"push","origin","master"])
        self._git(["-C",str(self.repo),"fetch","origin","master"])
        merged=self._git(["-C",str(self.repo),"rev-parse","origin/master"])
        self.controller.reconciler.revision=merged
        reconciled=self.controller.sweep(OWNER)
        self.assertEqual(reconciled[0]["state"],"reconciled")
        self.assertEqual(self.controller.get(OWNER,self.candidate_id)["merged_revision"],merged)

    def _merge_candidate_reviewed(self):
        self._advance_to_canary(); self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))
        published=self.controller.publish(OWNER,self.candidate_id,str(uuid.uuid4()))
        branch=published["branch"]
        self._git(["-C",str(self.repo),"fetch","origin",branch])
        self._git(["-C",str(self.repo),"merge","--no-edit","--no-ff","FETCH_HEAD","-m","reviewed candidate merge"])
        self._git(["-C",str(self.repo),"push","origin","master"])
        self._git(["-C",str(self.repo),"fetch","origin","master"])
        merged=self._git(["-C",str(self.repo),"rev-parse","origin/master"])
        self.controller.reconciler.revision=merged
        self.controller.reconcile(OWNER,self.candidate_id,str(uuid.uuid4()))
        return merged

    def test_change_to_master_source_after_prepare_blocks_publication(self):
        self._advance_to_canary(); self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))
        path=self.repo/"config/assistant-briefings.json"
        value=json.loads(path.read_bytes()); value["formatting"]["max_chars"]=650; path.write_text(json.dumps(value,indent=2)+"\n")
        self._git(["-C",str(self.repo),"add",str(path)]); self._git(["-C",str(self.repo),"commit","-m","concurrent source change"])
        self._git(["-C",str(self.repo),"push","origin","master"])
        self._git(["-C",str(self.repo),"fetch","origin","master"])
        with self.assertRaises(ImprovementError): self.controller.publish(OWNER,self.candidate_id,str(uuid.uuid4()))

    def test_grant_scope_class_expiry_and_quota_are_enforced(self):
        self._advance_to_canary()
        self._grant(["attention-threshold"])
        with self.assertRaises(ImprovementError): self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))
        self._grant(["briefing-format"])
        self.controller._grant(OWNER)
        value=json.loads(self.grant_file.read_text()); value["principal_scope"]="family"; value.pop("signature")
        value["signature"]=hmac.new(b"k"*32,canonical(value),hashlib.sha256).hexdigest(); self.grant_file.write_text(json.dumps(value)); self.grant_file.chmod(0o600)
        with self.assertRaises(ImprovementError): self.controller.promote(OWNER,self.candidate_id,str(uuid.uuid4()))

    def test_config_keeps_disabled_defaults_and_fixed_gate_commands(self):
        cfg=load_controller_config(ROOT/"config/assistant-improvements.json")
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["source_file"],"config/assistant-briefings.json")
        self.assertEqual(cfg["fixed_validation"],[["python3","tests/assistant-context-briefings-test.py"],["bash","./scripts/validate-repository.sh"]])

    @unittest.skipUnless(sys.platform=="linux" and os.geteuid()==0,"requires Linux root to verify privilege drop")
    def test_root_snapshot_reads_uncheckpointed_wal_as_source_owner(self):
        import pwd
        owner=pwd.getpwnam("nobody"); source_dir=self.root/"context-owned"; source_dir.mkdir(mode=0o700)
        os.chown(source_dir,owner.pw_uid,owner.pw_gid)
        source=source_dir/"attention.sqlite3"
        AttentionLedger(source,dry_run=True)
        db=sqlite3.connect(source)
        try: db.execute("CREATE TABLE wal_fixture(value TEXT NOT NULL)"); db.commit()
        finally: db.close()
        os.chown(source,owner.pw_uid,owner.pw_gid); os.chmod(source,0o600); os.chmod(self.root,0o755)
        writer_code="""import sqlite3,sys\ndb=sqlite3.connect(sys.argv[1],timeout=5)\ndb.execute('PRAGMA journal_mode=WAL')\ndb.execute('INSERT INTO wal_fixture VALUES(?)',('uncheckpointed-owner-data',))\ndb.commit()\nprint('READY',flush=True)\nsys.stdin.readline()\ndb.close()\n"""
        def drop_privileges(): os.setgroups([owner.pw_gid]); os.setgid(owner.pw_gid); os.setuid(owner.pw_uid)
        writer=subprocess.Popen([sys.executable,"-c",writer_code,str(source)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,preexec_fn=drop_privileges)
        try:
            ready=writer.stdout.readline().strip()
            if ready!="READY": self.fail("source-owner writer failed: "+writer.stderr.read())
            wal=Path(str(source)+"-wal"); shm=Path(str(source)+"-shm")
            self.assertTrue(wal.exists()); self.assertEqual(wal.stat().st_uid,owner.pw_uid)
            source_uid=source.stat().st_uid
            scratch=self.root/"controller-state"
            with AttentionReadSnapshot(source,scratch) as snapshot:
                copied=sqlite3.connect(snapshot.path)
                try:
                    value=copied.execute("SELECT value FROM wal_fixture").fetchone()[0]
                finally: copied.close()
                self.assertEqual(value,"uncheckpointed-owner-data")
            self.assertEqual(source.stat().st_uid,source_uid)
            for sidecar in (wal,shm):
                if sidecar.exists(): self.assertEqual(sidecar.stat().st_uid,owner.pw_uid)
            self.assertTrue(scratch.exists()); self.assertEqual(scratch.stat().st_uid,0)
        finally:
            if writer.poll() is None:
                writer.stdin.write("\n"); writer.stdin.flush(); writer.communicate(timeout=5)

    def test_health_probe_sends_separate_operator_bearer_required_by_api(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                if self.headers.get("Authorization")!="Bearer separate-operator-token":
                    self.send_response(401); self.end_headers(); return
                body=b'{"ready":true}'
                self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            token=self.root/"health-token"; token.write_text("separate-operator-token\n"); token.chmod(0o600)
            probe=SystemdPolicyOps(token_file=token,test_mode=True)
            probe.HEALTH=f"http://127.0.0.1:{server.server_port}/v1/health"
            self.assertTrue(probe.health()); self.assertEqual(seen,["Bearer separate-operator-token"])
            with self.assertRaises(ImprovementError): SystemdPolicyOps(token_file=self.root/"missing-health-token",test_mode=True).health()
        finally: server.shutdown(); thread.join(timeout=2); server.server_close()

    def test_safe_initialization_creates_target_but_does_not_claim_activation(self):
        target=self.root/"managed"/"assistant-briefings.json"
        ops=FakePolicyOps()
        installer=PolicyInstaller(self.controller,repo=self.repo,state_dir=self.state,target=target,ops=ops,
            test_mode=True,system="Linux",euid=0)
        plan=installer.plan_initialize(self.revision)
        result=installer.apply(plan["transaction_id"],plan["plan_sha256"])
        self.assertEqual(result["status"],"initialized_not_active")
        self.assertEqual(target.read_bytes(),self.before)
        self.assertEqual(ops.calls,[])
        with self.assertRaises(ImprovementError): installer.plan_initialize(self.revision)

    def test_policy_apply_requires_active_managed_selector_and_exact_source(self):
        target=self.root/"managed"/"assistant-briefings.json"; target.parent.mkdir(); target.write_bytes(self.before)
        installer=PolicyInstaller(self.controller,repo=self.repo,state_dir=self.state,target=target,ops=FakePolicyOps(),
            test_mode=True,system="Linux",euid=0)
        with self.assertRaises(ImprovementError): installer.plan_candidate(self.revision,self.candidate_id,sha(self.before))

    def test_apply_health_failure_rolls_back_and_stale_rollback_is_refused(self):
        merged=self._merge_candidate_reviewed()
        target=self.root/"managed"/"assistant-briefings.json"; target.parent.mkdir(); target.write_bytes(self.before)
        selector="python3 assistant-context-server.py --briefings "+str(target)
        failed_ops=FakePolicyOps(selector,health=(False,True))
        installer=PolicyInstaller(self.controller,repo=self.repo,state_dir=self.state,target=target,ops=failed_ops,
            test_mode=True,system="Linux",euid=0)
        plan=installer.plan_candidate(merged,self.candidate_id,sha(self.before))
        result=installer.apply(plan["transaction_id"],plan["plan_sha256"])
        self.assertEqual(result["status"],"rolled_back"); self.assertEqual(target.read_bytes(),self.before)
        self.assertEqual(failed_ops.calls,["stop","start","health","stop","start","health"])

        success_ops=FakePolicyOps(selector,health=(True,))
        installer.ops=success_ops
        next_plan=installer.plan_candidate(merged,self.candidate_id,sha(self.before))
        applied=installer.apply(next_plan["transaction_id"],next_plan["plan_sha256"])
        self.assertEqual(applied["status"],"applied")
        rolled=installer.rollback(next_plan["transaction_id"],applied["sha256"])
        self.assertEqual(rolled["status"],"rolled_back"); self.assertEqual(target.read_bytes(),self.before)
        drift=json.loads(target.read_bytes()); drift["formatting"]["max_chars"]=550
        target.write_text(json.dumps(drift))
        with self.assertRaises(ImprovementError): installer.rollback(next_plan["transaction_id"],sha(target.read_bytes()))

    def test_apply_resumes_after_process_exit_between_policy_write_and_service_start(self):
        merged=self._merge_candidate_reviewed()
        target=self.root/"managed"/"assistant-briefings.json"; target.parent.mkdir(); target.write_bytes(self.before)
        selector="python3 assistant-context-server.py --briefings "+str(target)
        ops=FakePolicyOps(selector)
        installer=PolicyInstaller(self.controller,repo=self.repo,state_dir=self.state,target=target,ops=ops,
            test_mode=True,system="Linux",euid=0)
        plan=installer.plan_candidate(merged,self.candidate_id,sha(self.before))
        atomic_write=installer._atomic_write; crashed=[False]
        def write_then_crash(raw):
            atomic_write(raw)
            if raw!=self.before and not crashed[0]:
                crashed[0]=True; raise SimulatedCrash()
        installer._atomic_write=write_then_crash
        with self.assertRaises(SimulatedCrash): installer.apply(plan["transaction_id"],plan["plan_sha256"])
        self.assertEqual(sha(target.read_bytes()),self.controller.get(OWNER,self.candidate_id)["after_sha"])
        self.assertFalse(ops.running)
        installer._atomic_write=atomic_write
        result=installer.apply(plan["transaction_id"],plan["plan_sha256"])
        self.assertEqual(result["status"],"applied"); self.assertTrue(ops.running)
        self.assertEqual(self.controller.get(OWNER,self.candidate_id)["state"],"applied")


class sqlite3_connect:
    def __init__(self,path):
        import sqlite3; self.db=sqlite3.connect(path)
    def __enter__(self): return self.db
    def __exit__(self,*args): self.db.commit(); self.db.close()

if __name__=="__main__": unittest.main()
