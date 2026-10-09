#!/usr/bin/env python3
"""One synthetic local-model/tool-loop probe; no worker, approval or publishing."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/codex-worker"))
from broker import Ledger, serve
from publisher import Publisher


def client_environment(path: Path) -> dict[str, str]:
    """Parse only literal client values, never execute/source shell commands."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        if name not in {"HOMECOMPUTE_SCRIPT_API_KEY", "NODE_EXTRA_CA_CERTS"}:
            continue
        parts = shlex.split(value)
        if len(parts) != 1:
            raise ValueError("expected a single literal client value")
        values[name] = os.path.expanduser(os.path.expandvars(parts[0]))
    if not values.get("HOMECOMPUTE_SCRIPT_API_KEY"):
        raise ValueError("scoped model client key is unavailable")
    if not Path(values.get("NODE_EXTRA_CA_CERTS", "")).is_file():
        raise ValueError("existing public HomeCompute CA file is unavailable")
    return values


def probe(cli: str, clients: Path) -> dict[str, object]:
    values = client_environment(clients)
    version = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=15)
    if version.returncode or "2026.9.9" not in version.stdout:
        raise ValueError("probe requires the pinned OpenClaw 2026.9.9 CLI")
    with tempfile.TemporaryDirectory(prefix="homecompute-openclaw-") as temporary:
        trial = Path(temporary)
        plugin = trial / "plugin"
        shutil.copytree(ROOT / "deploy/openclaw/broker-plugin", plugin)
        policy = {"synthetic-demo": {"repository": "example/synthetic", "base_sha": "1" * 40,
                  "base_branch": "main", "classification": "cloud_allowed", "tests": ["true"],
                  "write_prefixes": ["src/"]}}
        ledger = Ledger(trial / "tasks.sqlite3", policy)
        tokens = {name: "synthetic-" + name + "-" + uuid.uuid4().hex
                  for name in ("assistant", "worker", "operator")}
        server = serve(ledger, tokens, Publisher(trial / "absent-publish-key"), ("127.0.0.1", 0))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        transport = plugin / "transport.mjs"
        transport.write_text(transport.read_text().replace(
            "http://broker:8080", f"http://127.0.0.1:{server.server_port}"))
        cfg = json.loads((ROOT / "config/openclaw.json").read_text())
        cfg["plugins"]["load"]["paths"] = [str(plugin)]
        defaults = cfg["agents"]["defaults"]
        defaults.update(workspace=str(trial / "workspace"), skipBootstrap=True,
                        timeoutSeconds=90, contextInjection="never")
        provider = cfg["models"]["providers"]["homecompute"]
        # Local test bypasses the container-only relay; upstream remains fixed/CA-verified.
        provider.update(baseUrl="https://ai.home.arpa/v1", timeoutSeconds=90)
        provider.pop("request", None)
        provider["models"][0]["maxTokens"] = 1024
        cfg["tools"]["allow"] = ["homecompute_task_submit"]
        cfg["tools"]["toolSearch"] = {"enabled": False}
        cfg["gateway"]["bind"] = "loopback"
        config_path = trial / "openclaw.json"
        config_path.write_text(json.dumps(cfg))
        config_path.chmod(0o600)
        env = {name: os.environ[name] for name in ("PATH", "TMPDIR", "LANG", "LC_ALL") if name in os.environ}
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)
        env.update(HOME=str(trial), OPENCLAW_CONFIG_PATH=str(config_path),
                   OPENCLAW_CONFIG_READONLY="1", OPENCLAW_HOME=str(trial),
                   OPENCLAW_STATE_DIR=str(trial / "state"), OPENCLAW_GATEWAY_TOKEN=tokens["operator"],
                   OPENCLAW_MODEL_KEY=values["HOMECOMPUTE_SCRIPT_API_KEY"],
                   NODE_EXTRA_CA_CERTS=values["NODE_EXTRA_CA_CERTS"],
                   OPENCLAW_BROKER_TOKEN=tokens["assistant"])
        issue = "synthetic-runtime-probe-" + uuid.uuid4().hex
        message = ('Synthetic integration test. Call homecompute_task_submit exactly once with '
                   'project="synthetic-demo", issue_key="' + issue + '", '
                   'summary="Synthetic integration probe", '
                   'context="No production data. Do not approve execution or publish.". '
                   'Then report only the task id and pending state. Do not retry or use other tools.')
        try:
            result = subprocess.run([cli, "agent", "--local", "--agent", "assistant",
                                     "--session-id", str(uuid.uuid4()), "--message", message,
                                     "--timeout", "90", "--thinking", "off", "--json"],
                                    env=env, capture_output=True, text=True, timeout=120)
            parsed = json.loads(result.stdout.replace(env["OPENCLAW_MODEL_KEY"], "[redacted]"))
            meta = parsed.get("meta", {})
            agent = meta.get("agentMeta", {})
            tasks = ledger.list_tasks()
            receipt = agent.get("terminalReceipt", {})
            passed = (result.returncode == 0 and len(tasks) == 1 and tasks[0]["state"] == "pending"
                      and receipt.get("successfulToolNames") == ["homecompute_task_submit"]
                      and receipt.get("rerouted") is False)
            return {"passed": passed, "exit_code": result.returncode,
                    "duration_ms": meta.get("durationMs"), "provider": agent.get("provider"),
                    "model": agent.get("model"), "runtime": agent.get("agentHarnessId"),
                    "assistant_turns": agent.get("assistantTurns"), "receipt": receipt,
                    "payloads": parsed.get("payloads"), "broker_tasks": tasks,
                    "error": meta.get("error"), "scope": "synthetic broker; direct local inference only"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "external 120-second deadline exceeded",
                    "broker_tasks": ledger.list_tasks()}
        finally:
            server.shutdown()
            server.server_close()
            ledger.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", default=shutil.which("openclaw"))
    parser.add_argument("--clients", type=Path, default=Path.home() / ".config/homecompute/clients.env")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.cli:
        parser.error("pass --cli for an isolated pinned OpenClaw installation")
    record = probe(args.cli, args.clients)
    encoded = json.dumps(record, indent=2) + "\n"
    if args.report:
        args.report.write_text(encoded)
    print(encoded, end="")
    raise SystemExit(0 if record["passed"] else 1)


if __name__ == "__main__":
    main()
