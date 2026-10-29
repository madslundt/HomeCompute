"""Readable machine summaries from already validated observation envelopes."""
from __future__ import annotations

from typing import Any

LABELS = {"package-update-report": "Packages", "docker-image-report": "Container images",
          "n8n-workflow-failures": "n8n production workflow failures (last 15 minutes)",
          "mcp-update-report": "MCP update review", "mcp-update-report.coverage": "MCP update coverage",
          "host-update-report": "Host software updates", "host-update-report.coverage": "Host update coverage",
          "package-update-report.coverage": "Package update coverage",
          "docker-image-report.coverage": "Container image coverage",
          "package-update-report.reboot-required": "Reboot required",
          "ha.battery_optimizer": "Battery optimizer", "ha.battery_telemetry": "Battery telemetry",
          "ha.grid_meter": "Whole-house grid meter", "ha.tariff": "Electricity tariff",
          "ha.operation_plan": "Battery operation plan", "ha.grid_availability": "Grid availability",
          "collection": "Monitoring connection",
          "model-update-report.changed": "Model changes",
          "model-update-report.pin_drift": "Model version mismatches",
          "model-update-report.source_errors": "Model source errors",
          "model-update-report.outperforms_active": "Models outperforming the current model"}


def machine(body: dict[str, Any]) -> str | None:
    value = body.get("observation", {}).get("system_id")
    return value if isinstance(value, str) else None


def check_label(check: str) -> str:
    if check in LABELS:
        return LABELS[check]
    if check.startswith("ha.update."):
        return "HA update: " + check[len("ha.update."):].replace("_", " ")
    if check.startswith("container."):
        return "Container " + check[10:]
    if check.startswith("unit."):
        return "Service " + check[5:]
    return check


def summary_line(kind: str, observation: dict[str, Any]) -> str:
    check = observation["check_id"]
    label = check_label(check)
    if kind == "recovery":
        return "• Recovered: " + label
    evidence = observation["evidence"]
    if check == "package-update-report" and "candidate_count" in evidence:
        detail = f"{evidence['candidate_count']} updates available"
        if "security_count" in evidence:
            detail += f" ({evidence['security_count']} security)"
        if evidence.get("update_scope") == "package_catalog":
            detail += "; package catalog, installed status unverified"
    elif check == "docker-image-report" and "candidate_count" in evidence:
        detail = f"{evidence['candidate_count']} image updates available"
    elif check == "mcp-update-report" and "candidate_count" in evidence:
        details = []
        if evidence["candidate_count"]:
            details.append(f"{evidence['candidate_count']} major updates require approval; inspect maintenance_list, "
                           "then maintenance_prepare a fresh exact plan before requesting approval")
        if evidence.get("count"):
            details.append(f"{evidence['count']} updates held for review after failures or incomplete checks")
        detail = "; ".join(details) or "updates current"
    elif check == "host-update-report" and "candidate_count" in evidence:
        detail = (f"{evidence['candidate_count']} candidates need review or approval; "
                  f"{evidence.get('count', 0)} updates blocked or uncertain. "
                  "Inspect maintenance_list, then maintenance_prepare a fresh exact plan for "
                  "image-<container>, nixos, or packages before requesting approval")
    elif check.endswith(".coverage") and "count" in evidence:
        # Package coverage is a flag; empty Docker inventory also uses a
        # sentinel. Do not imply these are exact missing-source/image totals.
        detail = "incomplete or unverified"
        if check.startswith("docker-image-report"):
            detail += f" (reported gaps: {evidence['count']})"
    elif check.endswith(".reboot-required"):
        detail = "yes" if evidence.get("available") is True else "unknown"
    else:
        fields = []
        for key in ("state", "reason", "installed_version", "latest_version", "count", "candidate_count", "security_count"):
            if key in evidence:
                value = str(evidence[key]).replace("_", " ")
                fields.append(value if key in {"state", "reason", "count"} else key.replace("_", " ") + ": " + value)
        if "failed" in evidence:
            fields.append("failed" if evidence["failed"] else "not failed")
        if "available" in evidence:
            fields.append("available" if evidence["available"] else "unavailable")
        detail = "; ".join(fields) or "needs review"
    return "• " + label + ": " + detail


def render(system: str, members: list[tuple[str, dict[str, Any]]]) -> str:
    incidents = [(kind, body) for kind, body in members if kind != "recovery"]
    recoveries = [(kind, body) for kind, body in members if kind == "recovery"]
    counts = []
    if incidents:
        counts.append(f"{len(incidents)} check{'s need' if len(incidents) != 1 else ' needs'} review")
    if recoveries:
        counts.append(f"{len(recoveries)} check{'s' if len(recoveries) != 1 else ''} recovered")
    summary = system + ": " + "; ".join(counts) + "."
    if incidents:
        summary += " First: " + check_label(incidents[0][1]["observation"]["check_id"]) + "."
    if len(summary) > 220:
        summary = summary[:219].rstrip() + "…"
    lines = [summary, "", "**Machine summary · " + system + "**"]
    for heading, group in (("Needs review", incidents), ("Recovered checks", recoveries)):
        if group:
            lines.extend(("", "**" + heading + "**", ""))
            lines.extend("- " + summary_line(kind, body["observation"])[2:] for kind, body in group)
    checks = [body["observation"]["check_id"] for _, body in members]
    footer = ("Routine patch/minor updates deploy automatically after checks; major changes, uncertain results, "
              "and build gaps stay held for review and approval."
              if any(check.startswith("host-update-report") for check in checks)
              else "MCP patch/minor updates deploy automatically after checks; major updates require approval."
              if any(check.startswith("mcp-update-report") for check in checks)
              else "Review only; actions require operator approval.")
    lines.extend(("", "**Action policy**", "", footer))
    return "\n".join(lines)
