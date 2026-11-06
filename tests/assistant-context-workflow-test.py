"""Fail-closed n8n routine patch builder tests; all graphs are synthetic."""
from __future__ import annotations

from copy import deepcopy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_assistant_context as routine_patch


def graph_for(routine_id: str, *, duplicate_sender: bool = False):
    workflow_id = routine_patch.ROUTINES[routine_id]["workflow_id"]
    nodes = [
        {"id": "schedule", "name": "Existing schedule", "type": "n8n-nodes-base.scheduleTrigger",
         "typeVersion": 1.3, "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "5 5 * * *"}]}}},
        {"id": "guard", "name": "Existing once per owner guard", "type": "n8n-nodes-base.code",
         "typeVersion": 2, "parameters": {"mode": "runOnceForAllItems", "jsCode": "return $input.all();"}},
        {"id": "owner-send", "name": "Send private owner briefing", "type": "n8n-nodes-base.telegram",
         "typeVersion": 1.2, "position": [800, 200], "parameters": {"resource": "message", "operation": "sendMessage",
           "chatId": "sanitized-owner-private", "text": "={{ $json.message }}"},
         "credentials": {"telegramApi": {"id": "sanitized-credential", "name": "Private owner bot"}},
         "retryOnFail": True},
    ]
    connections = {"schedule": {"main": [[{"node": "guard", "type": "main", "index": 0}]]},
                   "guard": {"main": [[{"node": "owner-send", "type": "main", "index": 0}]]}}
    if duplicate_sender:
        nodes.append({"id": "second-send", "name": "Second message", "type": "n8n-nodes-base.telegram",
                      "typeVersion": 1.2, "parameters": {"resource": "message", "operation": "sendMessage",
                      "chatId": "sanitized-second-recipient", "text": "second"}})
        connections["guard"]["main"][0].append({"node": "second-send", "type": "main", "index": 0})
    return {"id": workflow_id, "name": "Synthetic only", "nodes": nodes,
            "connections": connections, "settings": {"timezone": "Europe/Copenhagen"}}


def build(graph, routine_id):
    node = next(item for item in graph["nodes"] if item["id"] == "owner-send")
    graph_hash = routine_patch.sha256(graph)
    node_hash = routine_patch.sha256(node)
    credential = {"id": "dedicated-routine-header", "name": "Assistant context routine relay"}
    guards = ["guard"]
    proof = {"workflow_id": graph["id"], "graph_sha256": graph_hash,
             "delivery_node_id": "owner-send", "delivery_node_sha256": node_hash,
             "principal": "owner", "visibility": "private", "single_recipient": True,
             "guard_node_ids": guards}
    return routine_patch.build_patch(graph, routine_id=routine_id, expected_graph_sha256=graph_hash,
        owner_delivery_node_id="owner-send", owner_delivery_node_name=node["name"],
        owner_delivery_node_type=node["type"], owner_delivery_node_sha256=node_hash,
        guard_node_ids=guards, credential_reference=credential, owner_edge_review=proof)


class RoutinePatchTests(unittest.TestCase):
    def test_source_templates_bind_existing_workflows_and_run_schedule(self):
        self.assertEqual(routine_patch.ROUTINES["morning-overview"]["workflow_id"], "WxRovre260928abc")
        self.assertEqual(routine_patch.ROUTINES["tomorrow-preparation"]["workflow_id"], "WKu7Zv4dE4ztnaRY")
        self.assertEqual(routine_patch.ROUTINES["morning-overview"]["existing_trigger_window"]["start"], "05:00")
        self.assertEqual(routine_patch.ROUTINES["tomorrow-preparation"]["existing_trigger_window"]["values"],
                         ["20:30", "20:45", "21:00", "21:15"])

    def test_replaces_one_existing_owner_sender_in_place(self):
        graph = graph_for("morning-overview")
        before = deepcopy(graph)
        result = build(graph, "morning-overview")
        after = result["workflow"]
        self.assertEqual(after["connections"], before["connections"])
        self.assertEqual(after["settings"], before["settings"])
        for node_id in ("schedule", "guard"):
            self.assertEqual(next(x for x in after["nodes"] if x["id"] == node_id),
                             next(x for x in before["nodes"] if x["id"] == node_id))
        send = next(x for x in after["nodes"] if x["id"] == "owner-send")
        self.assertEqual(send["type"], "n8n-nodes-base.httpRequest")
        self.assertEqual(send["typeVersion"], 4.5)
        self.assertEqual(send["parameters"]["url"], routine_patch.RELAY_URL)
        self.assertEqual(send["parameters"]["method"], "POST")
        self.assertEqual(send["parameters"]["genericAuthType"], "httpHeaderAuth")
        self.assertEqual(send["parameters"]["options"]["timeout"], 25000)
        self.assertEqual(send["credentials"], {"httpHeaderAuth": {"id": "dedicated-routine-header",
                                                                   "name": "Assistant context routine relay"}})
        self.assertIn("routine_id: 'morning-overview'", send["parameters"]["jsonBody"])
        self.assertIn("scheduled_for: $now.setZone('Europe/Copenhagen').toISO()", send["parameters"]["jsonBody"])
        self.assertFalse(send["retryOnFail"])
        self.assertEqual(result["qualification"], "synthetic source patch only; no live owner graph was inspected")

    def test_tomorrow_patches_only_the_existing_owner_sender(self):
        graph = graph_for("tomorrow-preparation")
        result = build(graph, "tomorrow-preparation")
        node = next(x for x in result["workflow"]["nodes"] if x["id"] == "owner-send")
        self.assertIn("routine_id: 'tomorrow-preparation'", node["parameters"]["jsonBody"])
        self.assertEqual(len(result["workflow"]["nodes"]), len(graph["nodes"]))

    def test_missing_graph_or_owner_edge_proof_fails_closed(self):
        with self.assertRaises(routine_patch.PatchRefused):
            routine_patch.build_patch({}, routine_id="morning-overview", expected_graph_sha256="0" * 64,
                owner_delivery_node_id="send", owner_delivery_node_name="Send", owner_delivery_node_type="n8n-nodes-base.telegram",
                owner_delivery_node_sha256="0" * 64, guard_node_ids=["guard"],
                credential_reference={"id": "cred", "name": "Header"}, owner_edge_review={})
        graph = graph_for("morning-overview")
        result = build(graph, "morning-overview")
        result["workflow"]["nodes"][1]["parameters"]["jsCode"] = "changed"
        with self.assertRaises(routine_patch.PatchRefused):
            routine_patch.build_patch(graph, routine_id="morning-overview", expected_graph_sha256="0" * 64,
                owner_delivery_node_id="owner-send", owner_delivery_node_name="Send private owner briefing",
                owner_delivery_node_type="n8n-nodes-base.telegram", owner_delivery_node_sha256="0" * 64,
                guard_node_ids=["guard"], credential_reference={"id": "cred", "name": "Header"}, owner_edge_review={})

    def test_duplicate_senders_wrong_node_unknown_credential_and_missing_guard_refused(self):
        for graph, routine in ((graph_for("morning-overview", duplicate_sender=True), "morning-overview"),):
            with self.assertRaisesRegex(routine_patch.PatchRefused, "multiple"):
                build(graph, routine)
        graph = graph_for("morning-overview")
        node = next(x for x in graph["nodes"] if x["id"] == "owner-send")
        gh, nh = routine_patch.sha256(graph), routine_patch.sha256(node)
        common = dict(routine_id="morning-overview", expected_graph_sha256=gh, owner_delivery_node_id="owner-send",
                      owner_delivery_node_name=node["name"], owner_delivery_node_type=node["type"],
                      owner_delivery_node_sha256=nh, guard_node_ids=["guard"],
                      credential_reference={"id": "dedicated-routine-header", "name": "Assistant context routine relay"},
                      owner_edge_review={"workflow_id": graph["id"], "graph_sha256": gh,
                          "delivery_node_id": "owner-send", "delivery_node_sha256": nh, "principal": "owner",
                          "visibility": "private", "single_recipient": True, "guard_node_ids": ["guard"]})
        bad_review = dict(common["owner_edge_review"], guard_node_ids=["missing"])
        bad = dict(common, guard_node_ids=["missing"], owner_edge_review=bad_review)
        with self.assertRaisesRegex(routine_patch.PatchRefused, "guard node missing"):
            routine_patch.build_patch(graph, **bad)
        bad = dict(common, credential_reference={"id": "placeholder", "name": "replace-me"})
        with self.assertRaisesRegex(routine_patch.PatchRefused, "credential"):
            routine_patch.build_patch(graph, **bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
