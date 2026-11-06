#!/usr/bin/env python3
"""Synthetic contracts for owner-scoped OpenClaw delegation and setup."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


d = load("assistant_context_delegation", ROOT / "scripts/assistant_context_delegation.py")
w = load("assistant_context_worker", ROOT / "scripts/assistant-context-worker.py")
s = load("setup_assistant_context_worker", ROOT / "scripts/setup-assistant-context-worker.py")


def config():
    value = json.loads((ROOT / "config/assistant-delegation.json").read_text())
    value["enabled"] = True
    return value


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.responses = []

    def call(self, operation, request, *, timeout=25):
        self.calls.append((operation, copy.deepcopy(request), timeout))
        if not self.responses:
            raise d.DelegationError("synthetic lost transport response")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.state_dir.mkdir(mode=0o700)
        self.state = self.state_dir / "delegation.sqlite3"
        os.chmod(self.state_dir, 0o700)
        self.transport = FakeTransport()
        self.now = 1794000000
        self.controller = d.DelegationController(self.state, config(), self.transport, clock=lambda: self.now)
        self.scope = d.OWNER_SCOPE
        self.request_id = str(uuid.uuid4())

    def tearDown(self):
        self.tmp.cleanup()

    def accepted(self, request_id, session_key):
        return {"schema_version": 1, "ok": True, "native": {
            "runId": request_id, "sessionKey": session_key,
            "agentId": d.ROLE, "status": "accepted", "acceptedAt": self.now}}

    def test_submit_persists_before_native_admission_and_returns_only_confirmed_acceptance(self):
        self.transport.responses.append(self.accepted(self.request_id, f"agent:{d.ROLE}:hc-{self.request_id}"))
        row = self.controller.submit(scope=self.scope, origin_thread="owner-dm:root-123",
                                     request_id=self.request_id, task_type="summary", task="Summarize fixture.")
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["native_run_id"], self.request_id)
        operation, request, _ = self.transport.calls[0]
        self.assertEqual(operation, "submit")
        self.assertEqual(request["request_id"], self.request_id)
        with self.assertRaises(d.DelegationError):
            self.controller.submit(scope=d.Scope("family", "household", "shared"),
                                   origin_thread="owner-dm:root-123", request_id=self.request_id,
                                   task_type="summary", task="Summarize fixture.")

    def test_unknown_response_is_durable_and_never_resubmitted(self):
        with self.assertRaisesRegex(d.DelegationError, "uncertain"):
            self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                                   task_type="analysis", task="Bounded task.")
        self.assertEqual(self.controller._current(self.scope, self.request_id)["status"], "uncertain")
        original_calls = len(self.transport.calls)
        self.now += 10
        row = self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                                     task_type="analysis", task="Bounded task.")
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(len(self.transport.calls), original_calls)

    def test_restart_converts_inflight_to_uncertain_and_native_timeout_does_not_claim_acceptance(self):
        with self.assertRaises(d.DelegationError):
            self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                                   task_type="analysis", task="Bounded task.")
        with self.controller._connect() as db:
            db.execute("UPDATE delegation_requests SET state='dispatching' WHERE request_id=?", (self.request_id,))
        second = d.DelegationController(self.state, config(), self.transport, clock=lambda: self.now + 1)
        self.assertEqual(second._current(self.scope, self.request_id)["status"], "uncertain")
        self.transport.responses.append({"schema_version": 1, "ok": True,
                                         "native": {"runId": self.request_id, "status": "timeout"}})
        row = second.status(scope=self.scope, request_id=self.request_id)
        self.assertEqual(row["status"], "uncertain")

    def test_native_status_reconciles_exact_run_and_terminal_evidence(self):
        self.transport.responses.append(self.accepted(self.request_id, f"agent:{d.ROLE}:hc-{self.request_id}"))
        self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                               task_type="analysis", task="Read supplied evidence.")
        self.transport.responses.append({"schema_version": 1, "ok": True,
            "native": {"runId": self.request_id, "status": "ok", "endedAt": self.now + 2,
                       "terminalReply": {"disposition": "visible", "text": "Result from fixture."},
                       "terminalReceipt": {"status": "ok", "toolCalls": 0}}})
        row = self.controller.status(scope=self.scope, request_id=self.request_id)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["result"], "Result from fixture.")
        self.assertEqual(row["evidence"]["native_run_id"], self.request_id)
        self.assertEqual(row["evidence"]["agent_id"], d.ROLE)

    def test_status_timeout_without_terminal_receipt_keeps_accepted_run_open(self):
        self.transport.responses.append(self.accepted(self.request_id, f"agent:{d.ROLE}:hc-{self.request_id}"))
        self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                               task_type="analysis", task="Bounded task.")
        self.transport.responses.append({"schema_version": 1, "ok": True,
                                         "native": {"runId": self.request_id, "status": "timeout"}})
        row = self.controller.status(scope=self.scope, request_id=self.request_id)
        self.assertEqual(row["status"], "accepted")
        self.assertIsNone(row["result"])

    def test_status_and_cancel_are_bound_to_trusted_origin(self):
        key = f"agent:{d.ROLE}:hc-{self.request_id}"
        self.transport.responses.append(self.accepted(self.request_id, key))
        self.controller.submit(scope=self.scope, origin_thread="owner-dm:root-a", request_id=self.request_id,
                               task_type="analysis", task="Private owner context.")
        calls_before = len(self.transport.calls)
        with self.assertRaises(d.DelegationError):
            self.controller.status(scope=self.scope, request_id=self.request_id,
                                   origin_thread="owner-dm:root-b")
        with self.assertRaises(d.DelegationError):
            self.controller.cancel(scope=self.scope, request_id=self.request_id,
                                   origin_thread="owner-dm:root-b")
        self.assertEqual(len(self.transport.calls), calls_before)
        self.assertEqual(self.controller._current(self.scope, self.request_id)["status"], "accepted")

    def test_cancel_uses_exact_native_agent_session_and_run(self):
        key = f"agent:{d.ROLE}:hc-{self.request_id}"
        self.transport.responses.append(self.accepted(self.request_id, key))
        self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                               task_type="summary", task="Bounded task.")
        self.transport.responses.append({"schema_version": 1, "ok": True,
                                         "native": {"status": "aborted", "runId": self.request_id,
                                                    "abortedRunId": self.request_id}})
        row = self.controller.cancel(scope=self.scope, request_id=self.request_id)
        self.assertEqual(row["status"], "cancelled")
        operation, request, _ = self.transport.calls[-1]
        self.assertEqual(operation, "cancel")
        self.assertEqual(request["request_id"], self.request_id)
        self.assertEqual(request["session_key"], key)

    def test_cancel_without_native_confirmation_stays_uncertain(self):
        key = f"agent:{d.ROLE}:hc-{self.request_id}"
        self.transport.responses.append(self.accepted(self.request_id, key))
        self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                               task_type="summary", task="Bounded task.")
        self.transport.responses.append({"schema_version": 1, "ok": True,
                                         "native": {"status": "no-active-run", "abortedRunId": None}})
        row = self.controller.cancel(scope=self.scope, request_id=self.request_id)
        self.assertEqual(row["status"], "cancel_requested")

    def test_duplicate_id_with_changed_origin_or_payload_is_rejected(self):
        self.transport.responses.append(self.accepted(self.request_id, f"agent:{d.ROLE}:hc-{self.request_id}"))
        self.controller.submit(scope=self.scope, origin_thread="thread-a", request_id=self.request_id,
                               task_type="summary", task="A.")
        with self.assertRaisesRegex(d.DelegationError, "different content"):
            self.controller.submit(scope=self.scope, origin_thread="thread-b", request_id=self.request_id,
                                   task_type="summary", task="A.")

    def test_task_types_expiry_budgets_and_scope_are_enforced(self):
        for kwargs in (
            {"task_type": "coding", "task": "x"},
            {"task_type": "analysis", "task": "x", "budget_seconds": 301},
            {"task_type": "analysis", "task": "x", "expires_at": self.now},
            {"task_type": "analysis", "task": "x" * 13000},
        ):
            with self.subTest(kwargs=tuple(kwargs)):
                with self.assertRaises(d.DelegationError):
                    self.controller.submit(scope=self.scope, origin_thread="thread-a",
                                           request_id=str(uuid.uuid4()), **kwargs)

    def test_source_refs_are_stored_scoped_and_removed_on_purge_before_native_cancel(self):
        ref = {"source": "aula", "object_id": "child/weekly-letter:2026-45/part-2", "revision": 4}
        self.transport.responses.append(self.accepted(self.request_id, f"agent:{d.ROLE}:hc-{self.request_id}"))
        self.controller.submit(scope=self.scope, origin_thread="owner-thread-1", request_id=self.request_id,
                               task_type="summary", task="Summarize this source.", source_refs=[ref])
        self.assertEqual(self.controller._current(self.scope, self.request_id)["source_refs"], [ref])
        self.transport.responses.append({"schema_version": 1, "ok": True, "native": {
            "status": "aborted", "runId": self.request_id, "abortedRunId": self.request_id}})
        result = self.controller.purge_source(scope=self.scope, source="aula",
                                              object_id="child/weekly-letter:2026-45/part-2")
        self.assertEqual(result["local_rows_scrubbed"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["pending_native_cleanup"][0]["native_cancel_status"], "confirmed-aborted")
        row = self.controller._current(self.scope, self.request_id)
        self.assertEqual(row["status"], "purge_pending")
        self.assertEqual(row["source_refs"], [])
        self.assertIsNone(row["result"])
        with self.controller._connect() as db:
            raw = dict(db.execute("SELECT task_text,result_text,source_refs_json,purge_keys_json FROM delegation_requests WHERE request_id=?",
                                  (self.request_id,)).fetchone())
        self.assertEqual(raw["task_text"], "")
        self.assertIsNone(raw["result_text"])
        self.assertEqual(json.loads(raw["source_refs_json"]), [])
        self.assertEqual(len(json.loads(raw["purge_keys_json"])), 1)
        # The native session deletion RPC archives rather than erases the
        # transcript on this pin, so the adapter must expose the remaining gate.
        self.assertEqual(result["pending_native_cleanup"][0]["native_transcript_cleanup"],
                         "not_supported_by_pinned_gateway_delete_contract")

    def test_source_reference_allowlist_is_enforced(self):
        with self.assertRaises(d.DelegationError):
            self.controller.submit(scope=self.scope, origin_thread="owner-thread-1", request_id=self.request_id,
                                   task_type="summary", task="Task.",
                                   source_refs=[{"source": "work:acme", "object_id": "x", "revision": "1"}])
        with self.assertRaises(d.DelegationError):
            self.controller.submit(scope=self.scope, origin_thread="owner-thread-1", request_id=self.request_id,
                                   task_type="summary", task="Task.",
                                   source_refs=[{"source": "calendar", "object_id": "event/1", "revision": "1"}])
        refs = [
            {"source": "calendar", "object_id": "event/UUID-550e8400-e29b-41d4-a716-446655440000", "revision": 9},
            {"source": "aula", "object_id": "MinUddannelse/weekly-letter/2026/45", "revision": 2**63 - 1},
        ]
        self.transport.responses.append(self.accepted(self.request_id, f"agent:{d.ROLE}:hc-{self.request_id}"))
        accepted = self.controller.submit(scope=self.scope, origin_thread="owner-thread-1",
            request_id=self.request_id, task_type="summary", task="Task.", source_refs=refs)
        self.assertEqual(accepted["source_refs"], refs)

    def test_revision_purge_preserves_newer_request_for_same_source_object(self):
        old_id, new_id = self.request_id, str(uuid.uuid4())
        old_ref = {"source": "calendar", "object_id": "same-event", "revision": 1}
        new_ref = {"source": "calendar", "object_id": "same-event", "revision": 2}
        self.transport.responses.append(self.accepted(old_id, f"agent:{d.ROLE}:hc-{old_id}"))
        self.controller.submit(scope=self.scope, origin_thread="owner-thread-3", request_id=old_id,
                               task_type="summary", task="Old calendar evidence.", source_refs=[old_ref])
        self.transport.responses.append({"schema_version": 1, "ok": True, "native": {
            "runId": old_id, "status": "ok", "endedAt": self.now + 1,
            "terminalReply": {"disposition": "visible", "text": "Old result."},
            "terminalReceipt": {"status": "ok", "toolCalls": 0}}})
        self.controller.status(scope=self.scope, request_id=old_id)
        self.transport.responses.append(self.accepted(new_id, f"agent:{d.ROLE}:hc-{new_id}"))
        self.controller.submit(scope=self.scope, origin_thread="owner-thread-3", request_id=new_id,
                               task_type="summary", task="Current calendar evidence.", source_refs=[new_ref])
        calls_before_purge = len(self.transport.calls)
        result = self.controller.purge_source(scope=self.scope, source="calendar",
                                              object_id="same-event", revision=1)
        self.assertEqual(result["local_rows_scrubbed"], 1)
        self.assertEqual(len(self.transport.calls), calls_before_purge)
        self.assertEqual(result["pending_native_cleanup"][0]["native_cancel_status"], "not_needed-terminal")
        self.assertEqual(self.controller._current(self.scope, old_id)["status"], "purge_pending")
        self.assertEqual(self.controller._current(self.scope, new_id)["status"], "accepted")
        with self.controller._connect() as db:
            new_row = db.execute("SELECT task_text,source_refs_json FROM delegation_requests WHERE request_id=?",
                                 (new_id,)).fetchone()
        self.assertEqual(new_row["task_text"], "Current calendar evidence.")
        self.assertEqual(json.loads(new_row["source_refs_json"]), [new_ref])

    def test_maintenance_mode_scrubs_existing_task_when_delegation_source_is_disabled(self):
        ref = {"source": "calendar", "object_id": "event-9", "revision": 2}
        self.transport.responses.append(self.accepted(self.request_id, f"agent:{d.ROLE}:hc-{self.request_id}"))
        self.controller.submit(scope=self.scope, origin_thread="owner-thread-2", request_id=self.request_id,
                               task_type="summary", task="Private source text.", source_refs=[ref])
        disabled = config()
        disabled["enabled"] = False
        maintenance = d.DelegationController(self.state, disabled, FakeTransport(),
                                               clock=lambda: self.now, maintenance_mode=True)
        with self.assertRaisesRegex(d.DelegationError, "disabled"):
            maintenance.submit(scope=self.scope, origin_thread="owner-thread-2", request_id=str(uuid.uuid4()),
                               task_type="summary", task="No new work.")
        result = maintenance.purge_source(scope=self.scope, source="calendar", object_id="event-9")
        self.assertEqual(result["local_rows_scrubbed"], 1)
        self.assertEqual(maintenance._current(self.scope, self.request_id)["status"], "purge_pending")
        self.assertEqual(maintenance._current(self.scope, self.request_id)["source_refs"], [])


class NativeWorkerTests(unittest.TestCase):
    def test_submit_uses_pinned_agent_rpc_and_requires_native_acceptance(self):
        req_id = str(uuid.uuid4())
        key = f"agent:{w.ROLE}:hc-{req_id}"
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "runId": req_id, "sessionKey": key, "agentId": w.ROLE,
                "status": "accepted", "acceptedAt": 123}), "")
        result = w.handle({"schema_version": 1, "operation": "submit", "request": {
            "operation": "submit", "request_id": req_id, "task_type": "summary",
            "session_key": key, "prompt": "Analyze the provided fixture.", "budget_seconds": 45}}, runner)
        self.assertEqual(result["native"]["runId"], req_id)
        command, kwargs = calls[0]
        self.assertEqual(command[2:4], ["call", "agent"])
        params = json.loads(command[command.index("--params") + 1])
        self.assertEqual(params["idempotencyKey"], req_id)
        self.assertEqual(params["agentId"], w.ROLE)
        self.assertEqual(params["sessionKey"], key)
        self.assertNotIn("Authorization", str(kwargs.get("env", {})))
        self.assertNotIn("token", params)

    def test_status_and_cancel_use_exact_pinned_native_methods(self):
        req_id = str(uuid.uuid4())
        key = f"agent:{w.ROLE}:hc-{req_id}"
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            method = command[3]
            payload = ({"runId": req_id, "status": "pending"} if method == "agent.wait" else
                       {"status": "aborted", "abortedRunId": req_id})
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        for operation, request in (("status", {"request_id": req_id, "session_key": key, "wait_ms": 0}),
                                   ("cancel", {"request_id": req_id, "session_key": key})):
            w.handle({"schema_version": 1, "operation": operation, "request": request}, runner)
        self.assertEqual([call[0][3] for call in calls], ["agent.wait", "sessions.abort"])
        cancel_params = json.loads(calls[1][0][calls[1][0].index("--params") + 1])
        self.assertEqual(cancel_params, {"key": key, "runId": req_id, "agentId": w.ROLE, "clearQueued": False})

    def test_mismatched_agent_session_method_and_task_type_are_rejected(self):
        req_id = str(uuid.uuid4())
        with self.assertRaises(w.WorkerRefused):
            w.handle({"schema_version": 1, "operation": "submit", "request": {
                "operation": "submit", "request_id": req_id, "task_type": "coding",
                "session_key": f"agent:{w.ROLE}:hc-{req_id}", "prompt": "x", "budget_seconds": 10}})
        with self.assertRaises(w.WorkerRefused):
            w.handle({"schema_version": 1, "operation": "status", "request": {
                "request_id": req_id, "session_key": f"agent:main:hc-{req_id}", "wait_ms": 0}})

    def test_fixed_ssh_transport_uses_approved_account_and_never_forwards_auth(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, '{"schema_version":1,"ok":true,"native":{}}', "")
        transport = d.FixedSSHTransport(runner=runner)
        transport.call("status", {"request_id": str(uuid.uuid4())})
        command, kwargs = calls[0]
        self.assertIn("hermes-operator@10.77.20.2", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("IdentityAgent=none", command)
        self.assertIn("ClearAllForwardings=yes", command)
        self.assertIn("NEMOCLAW_GATEWAY_PORT=9123", command[-1])
        self.assertIn("/run/credentials/homecompute-assistant-context.service/openclaw_worker_ssh_key", command)
        self.assertIn("UserKnownHostsFile=/run/credentials/homecompute-assistant-context.service/openclaw_worker_known_hosts", command)
        self.assertNotIn("/var/lib/homecompute-openclaw/.ssh/id_ed25519", command)
        self.assertNotIn("OPENCLAW_GATEWAY_TOKEN", kwargs.get("env", {}))
        self.assertNotIn("Authorization", str(kwargs.get("env", {})))
        with self.assertRaises(d.DelegationError):
            d.FixedSSHTransport(host="attacker.invalid", runner=runner)
        with self.assertRaises(d.DelegationError):
            d.FixedSSHTransport(worker="/sandbox/.openclaw/worker;touch /tmp/pwned", runner=runner)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.native = self.base / "openclaw.json"
        self.native.parent.mkdir(mode=0o700, exist_ok=True)
        agents = list(json.loads((ROOT / "config/openclaw-team.json").read_text())["agents"])
        original = {"agents": {"defaults": {"model": {"primary": "inference/automation-moe"}},
                               "entries": {role: {"name": role, "tools": {"allow": ["read"]}}
                                           for role in agents}},
                    "tools": {"allow": ["read", "write"]}, "plugins": {"entries": {"sample": {"enabled": True}}},
                    "bindings": [{"agentId": "main", "match": {"channel": "mattermost"}}],
                    "session": {"scope": "per-sender"}}
        self.before = (json.dumps(original, sort_keys=True, indent=2) + "\n").encode()
        self.native.write_bytes(self.before)
        os.chmod(self.native, 0o600)
        self.state = self.base / "receipts"
        s.DEFAULT_WORKER = self.base / "guest/homecompute-assistant-context-worker.py"
        s.DEFAULT_WORKSPACE = self.base / "guest/workspace-assistant-context-worker"
        self.worker = ROOT / "scripts/assistant-context-worker.py"
        self.workspace = ROOT / "deploy/openclaw/assistant-context-worker"
        (self.base / "guest").mkdir(mode=0o700)
        self.validations = []

    def tearDown(self):
        self.tmp.cleanup()

    def validator(self, command, **kwargs):
        self.validations.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "valid", "")

    def test_candidate_adds_only_dedicated_role_and_keeps_existing_policy(self):
        before = json.loads(self.before)
        after = s.candidate(before, s.read_source())
        self.assertEqual(set(after["agents"]["entries"]), set(before["agents"]["entries"]) | {"assistant-context-worker"})
        for key, value in before.items():
            if key != "agents":
                self.assertEqual(after[key], value)
        for role in before["agents"]["entries"]:
            self.assertEqual(after["agents"]["entries"][role], before["agents"]["entries"][role])
        role = after["agents"]["entries"]["assistant-context-worker"]
        self.assertEqual(role["tools"]["deny"], ["*"])
        self.assertEqual(role["tools"]["allow"], [])
        self.assertEqual(role["subagents"]["allowAgents"], [])

    def test_installer_plan_apply_and_exact_hash_rollback_preserve_native_state(self):
        plan = s.install(self.native, self.state, self.worker, self.workspace, runner=self.validator)
        self.assertFalse(plan["applied"])
        result = s.install(self.native, self.state, self.worker, self.workspace, apply=True,
                           expected_config_sha256=plan["before_config_sha256"], runner=self.validator)
        self.assertTrue(result["applied"])
        self.assertEqual(result["existing_roles_preserved"], 12)
        installed = json.loads(self.native.read_bytes())
        self.assertEqual(set(installed["agents"]["entries"]), set(json.loads(self.before)["agents"]["entries"]) | {"assistant-context-worker"})
        self.assertEqual(installed["tools"], json.loads(self.before)["tools"])
        self.assertEqual(installed["plugins"], json.loads(self.before)["plugins"])
        self.assertEqual(installed["bindings"], json.loads(self.before)["bindings"])
        receipt = s.rollback(self.native, self.state, result["backup_id"],
                             expected_config_sha256=result["installed_config_sha256"], runner=self.validator)
        self.assertTrue(receipt["rolled_back"])
        self.assertEqual(self.native.read_bytes(), self.before)
        self.assertTrue((s.DEFAULT_WORKSPACE / "AGENTS.md").exists())

    def test_unknown_roster_drift_stale_plan_and_unknown_role_overwrite_are_refused(self):
        before = json.loads(self.before)
        before["agents"]["entries"]["unknown"] = {}
        with self.assertRaisesRegex(s.SetupRefused, "roster drift"):
            s.candidate(before, s.read_source())
        plan = s.install(self.native, self.state, self.worker, self.workspace, runner=self.validator)
        with self.assertRaisesRegex(s.SetupRefused, "changed since reviewed plan"):
            s.install(self.native, self.state, self.worker, self.workspace, apply=True,
                      expected_config_sha256="0" * 64, runner=self.validator)
        before = json.loads(self.before)
        before["agents"]["entries"] = {role: {"name": role, "tools": {"allow": ["read"]}}
                                         for role in json.loads((ROOT / "config/openclaw-team.json").read_text())["agents"]}
        before["agents"]["entries"]["assistant-context-worker"] = {"tools": {"allow": ["read"]}}
        with self.assertRaisesRegex(s.SetupRefused, "differs"):
            s.candidate(before, s.read_source())

    def test_failed_final_validation_restores_config_and_removes_new_worker_files(self):
        plan = s.install(self.native, self.state, self.worker, self.workspace, runner=self.validator)
        calls = 0

        def fail_on_installed_config(command, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                return subprocess.CompletedProcess(command, 1, "", "invalid")
            return subprocess.CompletedProcess(command, 0, "valid", "")

        with self.assertRaisesRegex(s.SetupRefused, "validation failed"):
            s.install(self.native, self.state, self.worker, self.workspace, apply=True,
                      expected_config_sha256=plan["before_config_sha256"], runner=fail_on_installed_config)
        self.assertEqual(calls, 2)
        self.assertEqual(self.native.read_bytes(), self.before)
        self.assertFalse(s.DEFAULT_WORKER.exists())
        self.assertFalse((s.DEFAULT_WORKSPACE / "AGENTS.md").exists())
        self.assertFalse(s.DEFAULT_WORKSPACE.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
