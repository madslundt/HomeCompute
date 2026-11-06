"""Build preimage-pinned n8n patches for the two owner context routines.

This module is a local pure patch builder. It never fetches, saves, activates,
or executes a workflow. A sanitized graph plus explicit owner-edge review is
required; the checked-in schedule templates are not evidence of live wiring.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
RELAY_URL = "https://openclaw-communication.home.arpa:19443/assistant-context/routines/run"
N8N_VERSION = "2.46.0"
HTTP_REQUEST_VERSION = 4.5


def _load_routines() -> dict[str, dict[str, Any]]:
    result = {}
    for routine_file in sorted((ROOT / "automations/assistant-context").glob("routine-*.json")):
        row = json.loads(routine_file.read_text(encoding="utf-8"))
        if (not isinstance(row, dict) or row.get("schema_version") != 1
                or row.get("timezone") != "Europe/Copenhagen"
                or row.get("delivery_node_mode") != "replace-one-reviewed-owner-sender"):
            raise PatchRefused("reviewed routine source declaration required")
        routine_id = row.get("routine_id")
        if routine_id in result or routine_id not in {"morning-overview", "tomorrow-preparation"}:
            raise PatchRefused("duplicate or unknown routine source declaration")
        result[routine_id] = row
    if set(result) != {"morning-overview", "tomorrow-preparation"}:
        raise PatchRefused("both existing owner routine source declarations required")
    return result
MAX_GRAPH_BYTES = 8 * 1024 * 1024
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SEND_NODE_TYPES = {
    "n8n-nodes-base.telegram", "n8n-nodes-base.homeAssistant",
    "n8n-nodes-base.httpRequest", "n8n-nodes-base.slack",
    "n8n-nodes-base.discord", "n8n-nodes-base.emailSend",
    "n8n-nodes-base.gmail", "n8n-nodes-base.mattermost",
}


class PatchRefused(ValueError):
    """The selected graph cannot be safely qualified for this one-node patch."""


ROUTINES = _load_routines()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _recognized_send(node: Mapping[str, Any]) -> bool:
    """Recognize concrete outbound send nodes; unknown candidate types fail closed."""
    node_type = node.get("type")
    params = node.get("parameters")
    name = str(node.get("name", "")).lower()
    if node_type not in _SEND_NODE_TYPES or not isinstance(params, dict):
        return False
    if node_type == "n8n-nodes-base.httpRequest":
        url = params.get("url")
        return (params.get("method") == "POST" and isinstance(url, str)
                and any(marker in url.lower() for marker in (
                    "/api/v4/posts", "api.telegram.org/", "/messages", "/sendmessage", "/webhooks/")))
    if node_type == "n8n-nodes-base.homeAssistant":
        return params.get("resource") == "service" and params.get("operation") == "call" \
            and params.get("domain") == "notify"
    if node_type == "n8n-nodes-base.telegram":
        return params.get("resource") in {None, "message"} and params.get("operation") in {
            "sendMessage", "sendPhoto", "sendDocument", "sendMediaGroup", "sendVideo", "sendAudio"}
    if node_type in {"n8n-nodes-base.slack", "n8n-nodes-base.discord", "n8n-nodes-base.mattermost"}:
        operation = str(params.get("operation", "")).lower()
        return any(word in operation for word in ("send", "post", "create")) \
            or any(word in name for word in ("send", "post", "notify", "deliver"))
    if node_type in {"n8n-nodes-base.emailSend", "n8n-nodes-base.gmail"}:
        return any(word in str(params.get("operation", "send")).lower() for word in ("send", "create"))
    return False


def _reachable(graph: Mapping[str, Any], start_ids: list[str], target_id: str) -> bool:
    connections = graph.get("connections")
    if not isinstance(connections, dict):
        return False
    seen = set(start_ids)
    stack = list(start_ids)
    while stack:
        source = stack.pop()
        entry = connections.get(source, {})
        if not isinstance(entry, dict):
            continue
        for outputs in entry.values():
            if not isinstance(outputs, list):
                continue
            for branch in outputs:
                if not isinstance(branch, list):
                    continue
                for edge in branch:
                    if not isinstance(edge, dict):
                        continue
                    dest = edge.get("node")
                    if dest == target_id:
                        return True
                    if isinstance(dest, str) and dest not in seen:
                        seen.add(dest)
                        stack.append(dest)
    return False


def http_parameters(routine_id: str) -> dict[str, Any]:
    if routine_id not in ROUTINES:
        raise PatchRefused("source-owned routine required")
    expression = ("={{ { routine_id: '" + routine_id
                  + "', scheduled_for: $now.setZone('Europe/Copenhagen').toISO() } }}")
    return {
        "method": "POST",
        "url": RELAY_URL,
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": True,
        "contentType": "json",
        "specifyBody": "json",
        "jsonBody": expression,
        "options": {
            "timeout": 25000,
            "allowUnauthorizedCerts": False,
            "redirect": {"redirect": {"followRedirects": False}},
            "response": {"response": {"responseFormat": "json", "fullResponse": True}},
        },
    }


def build_patch(graph: dict[str, Any], *, routine_id: str, expected_graph_sha256: str,
                owner_delivery_node_id: str, owner_delivery_node_name: str,
                owner_delivery_node_type: str, owner_delivery_node_sha256: str,
                guard_node_ids: list[str], credential_reference: dict[str, str],
                owner_edge_review: dict[str, Any]) -> dict[str, Any]:
    """Replace exactly one reviewed owner sender in-place; retain every other edge/node.

    `owner_edge_review` is produced by a human review of the fresh, sanitized
    preimage, and binds the workflow, graph hash, one owner-private recipient,
    selected sender and preserved occurrence guards. It contains no destination
    ID, secret, or model-controlled routing field.
    """
    spec = ROUTINES.get(routine_id)
    if spec is None or not isinstance(graph, dict) or graph.get("id") != spec["workflow_id"]:
        raise PatchRefused("exact configured routine workflow required")
    encoded = canonical(graph)
    if len(encoded) > MAX_GRAPH_BYTES or hashlib.sha256(encoded).hexdigest() != expected_graph_sha256:
        raise PatchRefused("fresh sanitized workflow preimage mismatch")
    if (not isinstance(owner_delivery_node_id, str) or not NODE_ID.fullmatch(owner_delivery_node_id)
            or not isinstance(owner_delivery_node_name, str) or not owner_delivery_node_name.strip()
            or not isinstance(owner_delivery_node_type, str)
            or not isinstance(owner_delivery_node_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", owner_delivery_node_sha256)):
        raise PatchRefused("reviewed owner delivery node fingerprint required")
    if not isinstance(guard_node_ids, list) or not guard_node_ids or len(set(guard_node_ids)) != len(guard_node_ids):
        raise PatchRefused("existing occurrence guard node IDs required")
    if any(not isinstance(value, str) or not NODE_ID.fullmatch(value) for value in guard_node_ids):
        raise PatchRefused("invalid occurrence guard reference")
    expected_review = {
        "workflow_id": spec["workflow_id"], "graph_sha256": expected_graph_sha256,
        "delivery_node_id": owner_delivery_node_id,
        "delivery_node_sha256": owner_delivery_node_sha256,
        "principal": "owner", "visibility": "private", "single_recipient": True,
        "guard_node_ids": sorted(guard_node_ids),
    }
    if owner_edge_review != expected_review:
        raise PatchRefused("reviewed single owner-private delivery edge proof required")
    if (not isinstance(credential_reference, dict) or set(credential_reference) != {"id", "name"}
            or not isinstance(credential_reference["id"], str)
            or not NODE_ID.fullmatch(credential_reference["id"])
            or not isinstance(credential_reference["name"], str)
            or not credential_reference["name"].strip()
            or credential_reference["name"].lower().startswith(("replace", "example", "test"))):
        raise PatchRefused("dedicated external HTTP header credential reference required")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise PatchRefused("sanitized workflow node list required")
    selected = [node for node in nodes if node.get("id") == owner_delivery_node_id]
    if len(selected) != 1:
        raise PatchRefused("one reviewed old owner sender required")
    old = selected[0]
    if (old.get("name") != owner_delivery_node_name or old.get("type") != owner_delivery_node_type
            or not _recognized_send(old) or sha256(old) != owner_delivery_node_sha256):
        raise PatchRefused("selected node does not match the pinned owner sender")
    senders = [node for node in nodes if _recognized_send(node)]
    if len(senders) != 1 or senders[0].get("id") != owner_delivery_node_id:
        raise PatchRefused("workflow has multiple or unexpected outbound send nodes")
    ids = {node.get("id") for node in nodes}
    if any(node_id not in ids or node_id == owner_delivery_node_id for node_id in guard_node_ids):
        raise PatchRefused("guard node missing or points at the replaced sender")
    if not _reachable(graph, guard_node_ids, owner_delivery_node_id):
        raise PatchRefused("owner sender is not downstream of the reviewed occurrence guard")

    patched = deepcopy(graph)
    replacement = next(node for node in patched["nodes"] if node["id"] == owner_delivery_node_id)
    replacement["type"] = "n8n-nodes-base.httpRequest"
    replacement["typeVersion"] = HTTP_REQUEST_VERSION
    replacement["parameters"] = http_parameters(routine_id)
    replacement["credentials"] = {"httpHeaderAuth": deepcopy(credential_reference)}
    # Requests must not be blindly retried after an ambiguous relay response.
    replacement["retryOnFail"] = False
    if sha256(patched) == sha256(graph):
        raise PatchRefused("source patch did not change the selected delivery node")
    return {"workflow_id": spec["workflow_id"], "routine_id": routine_id,
            "preimage_sha256": expected_graph_sha256,
            "changed_node_id": owner_delivery_node_id,
            "preserved_connections": deepcopy(graph["connections"]), "workflow": patched,
            "qualification": "synthetic source patch only; no live owner graph was inspected"}
