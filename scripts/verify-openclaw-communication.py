#!/usr/bin/env python3
"""Live synthetic HTTP→fixed SSH→managed OpenClaw probe; mock notification receipt."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import urllib.request
import uuid

from openclaw_notifications import Outbox

ROOT = Path(__file__).resolve().parents[1]


def module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def probe() -> dict:
    communication = module("communication", "openclaw-communication.py")
    chat = module("chat", "openclaw-chat.py")
    name = "qualification-" + uuid.uuid4().hex[:12]
    settings = {"schema_version": 1, "destination": "synthetic-private", "conversations": [name],
                "projects": [], "actions": {}, "timezone": "Europe/Copenhagen",
                "quiet_start_hour": 22, "quiet_end_hour": 7, "cooldown_seconds": 1800}
    tokens = {x: "synthetic-" + x + "-" + uuid.uuid4().hex for x in ("conversation", "collector", "delivery")}
    calls = []
    with tempfile.TemporaryDirectory(prefix="hc-openclaw-communication-") as raw:
        directory = Path(raw)
        store = Outbox(directory / "transport.sqlite3", settings)
        def turn(conversation, text):
            if text.startswith("Read-only HomeCompute observation"):
                text = "Synthetic qualification fixture only; the reported service condition is invented. " + text
            answer = chat.send(text, conversation, directory=directory / "sessions")
            calls.append({"verified": answer["verified"], "provider": answer["provider"], "model": answer["model"], "session": answer["session_id"]})
            return answer["text"]
        server = communication.serve(store, tokens, communication.registry_keys(ROOT / "config/system-monitoring.json"), turn, ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def post(path, role, body):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + tokens[role], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=70) as response: return json.load(response)
        try:
            first = {"conversation": name, "destination": "synthetic-private", "request_id": "synthetic:1",
                     "text": "Synthetic conversation test only. Remember the word silver-lantern in this conversation; do not write memory or call tools. Reply exactly silver-lantern 42, because 6 times 7 is 42."}
            response = post("/conversation", "conversation", first)
            post("/conversation", "conversation", first)
            second = dict(first, request_id="synthetic:2", text="Synthetic test. Without tools, reply only with the word and arithmetic answer from my preceding message.")
            recalled = post("/conversation", "conversation", second)
            receipts = []
            for number in (1, 2):
                event = post("/claim", "delivery", {})["event"]
                receipt = {"delivery_key": event["delivery_key"], "claim": event["claim"], "receipt": f"synthetic:{number}"}
                post("/ack", "delivery", receipt)
                receipts.append({"kind": event["kind"], "destination": event["destination"]})
            status = post("/status", "conversation", {})
            duplicate_avoided = len(calls) == 2
            now = time.time()
            timestamp = lambda val: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(val))
            key = "home-core:container.homecompute-automation-n8n-1"
            post("/observations", "collector", {"schema_version": 1, "document_type": "system_observation_report",
                 "generated_at": timestamp(now), "mode": "observe-only", "automatic_actions": False,
                 "physical_device_actions": False, "observations": [{"system_id": "home-core", "check_id": key.split(":", 1)[1],
                    "stable_key": key, "category": "health", "status": "degraded", "severity": "warning",
                    "evidence": {"state": "unhealthy"}, "observed_at": timestamp(now), "expires_at": timestamp(now + 900)}]})
            incident_key = store.db.execute("SELECT key FROM events WHERE kind='incident'").fetchone()[0]
            analysis = post("/analysis", "collector", {"delivery_key": incident_key})
            passed = (len(calls) == 3 and calls[0]["session"] == calls[1]["session"]
                      and response["state"] == recalled["state"] == "completed"
                      and "silver-lantern" in recalled["reply"] and "42" in recalled["reply"]
                      and status["outbox"] == {"delivered": 2}
                      and analysis["state"] == "completed" and bool(analysis["reply"]))
            return {"passed": passed, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "scope": "synthetic managed conversation and read-only incident analysis; mock outbox delivery acknowledgements",
                    "verified_native_turns": len(calls), "same_conversation_session": calls[0]["session"] == calls[1]["session"] if len(calls) > 1 else False,
                    "provider": calls[0]["provider"] if calls else None, "model": calls[0]["model"] if calls else None,
                    "duplicate_request_did_not_rerun": duplicate_avoided, "native_observation_analysis_completed": analysis["state"] == "completed", "receipts": receipts,
                    "outbox": status["outbox"], "external_messages_sent": 0,
                    "live_configuration_changed": False, "secrets_provisioned": False}
        finally:
            server.shutdown(); server.server_close(); thread.join(); store.db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    receipt = probe()
    output = json.dumps(receipt, indent=2) + "\n"
    if args.report: args.report.write_text(output)
    print(output, end="")
    raise SystemExit(0 if receipt["passed"] else 1)
