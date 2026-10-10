"""Readable machine summaries from already validated observation envelopes."""
from __future__ import annotations

from typing import Any

LABELS = {"package-update-report": "Packages", "docker-image-report": "Container images",
          "package-update-report.coverage": "Package update coverage",
          "docker-image-report.coverage": "Container image coverage",
          "package-update-report.reboot-required": "Reboot required",
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
        for key in ("state", "reason", "installed_version", "count", "candidate_count", "security_count"):
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
    lines = [system + " — machine summary"]
    lines.extend(summary_line(kind, body["observation"]) for kind, body in members)
    lines.extend(("", "Review only; actions require operator approval."))
    return "\n".join(lines)
