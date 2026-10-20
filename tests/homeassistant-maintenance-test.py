#!/usr/bin/env python3
"""Exact HA versions/capabilities, root plans, and independently enforced n8n gates."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ha = load("ha_maintenance", "scripts/homeassistant-maintenance.py")
setup = load("ha_maintenance_setup", "scripts/setup-homeassistant-maintenance.py")
from infrastructure_maintenance import validate_plan

ENTITY = "update.home_assistant_core_update"
NOW = 1791892800
TEMPLATE = ROOT / "automations/agent-investigation/n8n-ha-maintenance-workflow.json"

def snapshot(**changes):
    return {"schema_version": 1, "entity_id": ENTITY, "state": "on", "installed_version": "2026.10.1",
            "latest_version": "2026.10.2", "supported_features": 11, "in_progress": False,
            "observed_at": NOW, **changes}


class Tests(unittest.TestCase):
    def test_root_boundary_precedes_credentials_and_cli_work(self):
        with patch.object(sys, "argv", ["ha", "snapshot", ENTITY]), \
             patch.object(ha.os, "geteuid", return_value=501), \
             patch.object(ha, "Transport", side_effect=AssertionError("must not read token")), \
             patch.object(sys, "stderr"), self.assertRaises(SystemExit):
            ha.main()

    def test_exact_entity_and_stable_versions(self):
        for value in ("light.kitchen", "update.other", ENTITY + ",update.other", [ENTITY]):
            with self.assertRaises(ValueError): ha.entity(value)
        for value in ("latest", "2026.10.2-beta", "1.2.3;reboot", " 1.2.3", None):
            with self.assertRaises(ValueError): ha.version(value)
        self.assertEqual(ha.version("v1.2.3"), "v1.2.3")

    def test_metadata_rejects_stale_wrong_targets_private_fields_or_invalid_capabilities(self):
        for changes in ({"observed_at": NOW - 31}, {"observed_at": NOW + 3}, {"entity_id": "update.other"},
                        {"secret": "private"}, {"supported_features": True}, {"in_progress": 50},
                        {"schema_version": True}, {"state": "unavailable"}):
            with self.assertRaises(ValueError): ha.metadata(snapshot(**changes), ENTITY, NOW)

    def test_baseline_candidate_backup_features_and_state_checked_before_any_install(self):
        for changes in ({"installed_version": "2026.10.0"}, {"latest_version": "2026.10.3"},
                        {"supported_features": 1}, {"supported_features": 3}, {"supported_features": 9},
                        {"state": "off"}, {"in_progress": True}):
            calls = []
            def transport(body):
                calls.append(body)
                return snapshot(**changes)
            with self.assertRaises(ValueError): ha.Maintenance(transport, lambda: NOW).install(ENTITY, "2026.10.2", "2026.10.1")
            self.assertEqual(calls, [{"operation": "snapshot", "entity_id": ENTITY}])

    def test_install_is_one_exact_backup_service_attempt_and_never_retries_uncertain_receipt(self):
        for receipt in ({"accepted": True, "entity_id": ENTITY, "version": "2026.10.2", "backup": True}, {}):
            calls = []
            def transport(body):
                calls.append(body)
                return snapshot() if body["operation"] == "snapshot" else receipt
            helper = ha.Maintenance(transport, lambda: NOW)
            if receipt:
                self.assertEqual(helper.install(ENTITY, "2026.10.2", "2026.10.1"), receipt)
            else:
                with self.assertRaises(ValueError): helper.install(ENTITY, "2026.10.2", "2026.10.1")
            self.assertEqual(calls[-1], {"operation": "execute", "entity_id": ENTITY, "version": "2026.10.2",
                                         "expected_installed": "2026.10.1", "backup": True})
            self.assertEqual(len(calls), 2)

    def test_prepared_plan_hash_is_stable_and_controller_classifies_patch_and_month_release(self):
        for candidate, required in (("2026.10.2", False), ("2026.11.0", True)):
            helper = ha.Maintenance(lambda body: snapshot(latest_version=candidate), lambda: NOW)
            plan = helper.prepare(ENTITY)
            self.assertEqual(validate_plan(plan)["approval_required"], required)
            self.assertEqual(plan["prechecks"][0]["stdout_sha256"], hashlib.sha256(ha.stable_output(snapshot(latest_version=candidate, observed_at=NOW+10))).hexdigest())
            self.assertNotIn("observed_at", ha.stable_output(snapshot()).decode())
            self.assertEqual(plan["steps"], [[ha.COMMAND, "install", ENTITY, candidate, "--expected-installed", "2026.10.1"]])
            self.assertEqual(plan["postchecks"], [[ha.COMMAND, "verify", ENTITY, candidate]])
        for changes in ({"state": "off"}, {"supported_features": 3}, {"in_progress": True}):
            with self.assertRaises(ValueError): ha.Maintenance(lambda body: snapshot(**changes), lambda: NOW).prepare(ENTITY)

    def test_verify_tolerates_restart_and_progress_but_only_repeats_snapshots(self):
        values = iter([OSError(), snapshot(in_progress=True), snapshot(installed_version="2026.10.2")])
        calls, slept = [], []
        def transport(body):
            calls.append(body)
            value = next(values)
            if isinstance(value, Exception): raise value
            return value
        helper = ha.Maintenance(transport, lambda: NOW)
        result = helper.verify(ENTITY, "2026.10.2", sleep=slept.append, monotonic=lambda: 0)
        self.assertEqual(result["installed_version"], "2026.10.2")
        self.assertEqual(slept, [5, 5])
        self.assertTrue(all(call["operation"] == "snapshot" for call in calls))
        with self.assertRaises(ValueError): ha.Maintenance(lambda body: snapshot(), lambda: NOW).verify(ENTITY, "2026.10.2", timeout=0, monotonic=lambda: 0)

    def test_workflow_template_is_pinned_closed_and_only_one_non_retrying_update_service_exists(self):
        flow = setup.validate_template(TEMPLATE)
        self.assertFalse(flow["active"])
        self.assertIn("configured=false", flow["nodes"][1]["parameters"]["jsCode"])
        calls = [n for n in flow["nodes"] if n["type"] == "n8n-nodes-base.homeAssistant" and n["parameters"]["resource"] == "service"]
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["parameters"]["domain"], calls[0]["parameters"]["service"]), ("update", "install"))
        self.assertIs(calls[0]["retryOnFail"], False)
        self.assertIn("value:true", calls[0]["parameters"]["serviceAttributes"])
        for key in ("saveDataSuccessExecution", "saveDataErrorExecution"):
            self.assertEqual(flow["settings"][key], "none")
        for key in ("saveManualExecutions", "saveExecutionProgress", "availableInMCP"):
            self.assertIs(flow["settings"][key], False)

    def test_provision_preserves_existing_records_and_installs_only_a_root_route_without_ha_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, state, helper = root / "n8n.sqlite", root / "state", root / "helper"
            with sqlite3.connect(database) as db:
                db.executescript("CREATE TABLE workflow_entity(id TEXT PRIMARY KEY,active INT,versionId TEXT,activeVersionId TEXT,updatedAt TEXT); CREATE TABLE credentials_entity(id TEXT PRIMARY KEY,name TEXT,type TEXT,updatedAt TEXT); CREATE TABLE shared_workflow(workflowId TEXT,projectId TEXT);")
                db.execute("INSERT INTO workflow_entity VALUES('hcOpenClawHA20261010',1,'retained','retained','retained-time')")
                db.execute("INSERT INTO credentials_entity VALUES(?, 'retained HA', 'homeAssistantApi','retained-time')", (setup.HA_CREDENTIAL,))
                db.execute("INSERT INTO shared_workflow VALUES('hcOpenClawHA20261010','project')")
            uploaded, commands = {}, []
            def call(argv, data=None):
                commands.append(argv)
                if data is not None:
                    uploaded['credential' if 'credential.json' in argv[-1] else 'workflow'] = json.loads(data)
                elif 'import:credentials' in argv:
                    with sqlite3.connect(database) as db:
                        c = uploaded['credential'][0]
                        db.execute("INSERT INTO credentials_entity VALUES(?,?,?,?)", (c['id'], c['name'], c['type'], 'new-time'))
                elif 'import:workflow' in argv:
                    with sqlite3.connect(database) as db:
                        flow = uploaded['workflow'][0]
                        db.execute("INSERT INTO workflow_entity VALUES(?,0,'new',NULL,'new-time')", (flow['id'],))
                elif 'publish:workflow' in argv:
                    with sqlite3.connect(database) as db:
                        db.execute("UPDATE workflow_entity SET active=1,activeVersionId=versionId WHERE id=?", (setup.WORKFLOW,))
            original_path = setup.Path
            def paths(value):
                return root / "backup" if str(value).startswith('/srv/backups/ha-maintenance-') else original_path(value)
            with patch.object(setup, "DATABASE", database), patch.object(setup, "STATE", state), \
                 patch.object(setup, "HELPER", helper), patch.object(setup, "call", side_effect=call), \
                 patch.object(setup, "Path", side_effect=paths), patch.object(setup, "trusted_parent"), \
                 patch.object(setup.os, "geteuid", return_value=0), patch.object(setup.os, "fchown"), \
                 patch.object(setup.socket, "gethostname", return_value='home-core'):
                result = setup.provision(TEMPLATE, ROOT / 'scripts/homeassistant-maintenance.py')
            self.assertEqual(result['existing_workflows_preserved'], 1)
            self.assertEqual(result['existing_credentials_preserved'], 1)
            self.assertEqual(result['ha_updates_executed'], 0)
            self.assertEqual((state / 'executor-token').stat().st_mode & 0o777, 0o600)
            self.assertEqual(helper.stat().st_mode & 0o777, 0o755)
            self.assertIn('configured=true', uploaded['workflow'][0]['nodes'][1]['parameters']['jsCode'])
            self.assertEqual(commands[-1], ['docker', 'restart', setup.CONTAINER])
            self.assertFalse(any('snapshot' in command or 'install' in command or 'update.install' in command for command in commands))
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT active,versionId,activeVersionId,updatedAt FROM workflow_entity WHERE id='hcOpenClawHA20261010'").fetchone(), (1,'retained','retained','retained-time'))

    def test_workflow_gate_and_independent_snapshot_validation_execute_in_node(self):
        flow = json.loads(TEMPLATE.read_text())
        js = """const vm=require('node:vm');const f=JSON.parse(process.argv[1]);
const gate=f.nodes[1].parameters.jsCode, check=f.nodes[3].parameters.jsCode;
function run(code,body,rows){return vm.runInNewContext('(function(){'+code+'})()',{$json:{body,query:{},params:{}},$input:{all:()=>rows||[{}]},$:()=>({first:()=>({json:{request:body}})})});}
const r={operation:'execute',entity_id:'update.home_assistant_core_update',version:'2026.10.2',expected_installed:'2026.10.1',backup:true};
function denied(code,body,rows){try{run(code,body,rows);}catch(e){return;}throw Error('unsafe request accepted');}
denied(gate,r);const open=gate.replace('configured=false','configured=true');run(open,r);
for(const change of [{entity_id:'light.kitchen'},{entity_id:['update.home_assistant_core_update']},{backup:false},{extra:'x'},{version:'latest'}])denied(open,{...r,...change});
const e={entity_id:r.entity_id,state:'on',attributes:{installed_version:r.expected_installed,latest_version:r.version,supported_features:11,in_progress:false}};
const result=run(check,r,[{json:e}]);if(result[0].json.snapshot.entity_id!==r.entity_id)throw Error('wrong snapshot');
for(const change of [{installed_version:'2026.10.0'},{latest_version:'2026.11.0'},{supported_features:3},{in_progress:true}])denied(check,r,[{json:{...e,attributes:{...e.attributes,...change}}}]);
console.log('validated');"""
        result = subprocess.run(["node", "-e", js, json.dumps(flow)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "validated")


if __name__ == "__main__":
    unittest.main()
