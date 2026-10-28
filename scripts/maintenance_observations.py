"""Finite update report projection; package names and registry data stay outside chat."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections import Counter
import hashlib
import json
from typing import Any

SOURCES = {"package-update-report", "docker-image-report", "mcp-update-report", "host-update-report"}
TTL = 129600  # A missed daily collection becomes stale after 36 hours.
MCP_TTL = 691200  # A missed weekly MCP run becomes stale after eight days.
MCP_STATUSES = {"current", "succeeded", "approval_required", "failed", "uncertain", "blocked"}
MCP_REASONS = {"not_checked", "operator_reconciliation_required", "fresh_exact_plan_requires_approval",
               "preparation_or_execution_refused"}
HOST_STATUSES = {"current", "succeeded", "approval_required", "review", "blocked", "failed", "uncertain",
                 "delegated", "available"}


def ttl(source: str) -> int:
    return MCP_TTL if source.split(".")[0] == "mcp-update-report" else TTL


def project_host(system: dict[str, Any], raw: dict[str, Any], now: datetime,
                 check: Any) -> list[dict[str, Any]]:
    """Expose finite counts while preserving receipt identity only as a digest."""
    source = "host-update-report"
    make = lambda key, state, evidence: check(system, key, state, evidence, now, updates=True)
    def unavailable(reason: str) -> list[dict[str, Any]]:
        return [make(source, "unknown", {"reason": reason}),
                make(source + ".coverage", "degraded", {"reason": reason})]
    try:
        report = raw.get("host_update_report")
        if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
                or report["schema_version"] != 1 or report.get("host") != system["target"]
                or system["target"] not in {"home-core", "home-spark"}):
            return unavailable("update_metadata_unavailable")
        generated = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError("timezone required")
        generated = generated.astimezone(timezone.utc)
        if not -30 <= (now - generated).total_seconds() <= TTL:
            return unavailable("stale")
        items, summary = report["items"], report["summary"]
        if (not isinstance(items, list) or len(items) > 256 or not isinstance(summary, dict)
                or any(key not in HOST_STATUSES or type(value) is not int or not 0 <= value <= 256
                       for key, value in summary.items())):
            raise ValueError("invalid host report inventory")
        selected = []
        for item in items:
            if (not isinstance(item, dict) or item.get("status") not in HOST_STATUSES
                    or item.get("kind") not in {"packages", "docker", "coverage"}
                    or not isinstance(item.get("component"), str) or not 1 <= len(item["component"]) <= 256):
                raise ValueError("invalid host result")
            row = {key: item[key] for key in ("component", "kind", "status")}
            for key in ("installed", "candidate", "plan_id", "reason"):
                if key in item:
                    if not isinstance(item[key], str) or len(item[key]) > 256:
                        raise ValueError("unbounded host metadata")
                    if key != "plan_id":
                        row[key] = item[key]
            selected.append(row)
        counts = Counter(item["status"] for item in selected)
        initial = not items and summary == {"blocked": 1}
        if {key: value for key, value in summary.items() if value} != dict(counts) and not initial:
            raise ValueError("host summary differs from actual results")
        if not items and not initial:
            raise ValueError("empty host inventory cannot claim current")
        candidates = sum(counts[key] for key in ("review", "approval_required", "available"))
        errors = 1 if initial else sum(counts[key] for key in ("blocked", "failed", "uncertain"))
        missing = errors + counts["review"]
        selected.sort(key=lambda row: json.dumps(row, sort_keys=True))
        stamp = generated.isoformat().replace("+00:00", "Z")
        provenance = {"source_report_generated_at": stamp, "source_digest": hashlib.sha256(
            json.dumps([selected, initial], sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        gaps = [row for row in selected if row["status"] in {"review", "blocked", "failed", "uncertain"}]
        coverage = dict(provenance, source_digest=hashlib.sha256(json.dumps(
            [gaps, initial], sort_keys=True, separators=(",", ":")).encode()).hexdigest(), count=missing)
        result = [make(source, "degraded" if candidates or errors else "healthy",
                       dict(provenance, candidate_count=candidates, count=errors)),
                  make(source + ".coverage", "degraded" if missing else "healthy", coverage)]
        for row in result:
            row["observed_at"] = stamp
            row["expires_at"] = (generated + timedelta(seconds=TTL)).isoformat().replace("+00:00", "Z")
        return result
    except (ValueError, KeyError, TypeError, OverflowError):
        return unavailable("unsupported_schema")


def project_mcp(system: dict[str, Any], raw: dict[str, Any], now: datetime,
                check: Any) -> list[dict[str, Any]]:
    """Project a bounded public result without admitting component details to chat."""
    source = "mcp-update-report"
    make = lambda key, state, evidence: check(system, key, state, evidence, now, updates=True)
    def unavailable(reason: str) -> list[dict[str, Any]]:
        return [make(source, "unknown", {"reason": reason}),
                make(source + ".coverage", "degraded", {"reason": reason})]
    try:
        report = raw.get("mcp_update_report")
        if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
                or report["schema_version"] != 1 or report.get("host") != "home-core"
                or system["target"] != "home-core"):
            return unavailable("update_metadata_unavailable")
        generated = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError("timezone required")
        generated = generated.astimezone(timezone.utc)
        if not -30 <= (now - generated).total_seconds() <= MCP_TTL:
            return unavailable("stale")
        components = report["components"]
        if (not isinstance(components, list) or len(components) != 2
                or any(not isinstance(row, dict) for row in components)
                or {row.get("component") for row in components} != {"aula-mcp", "tilbudstrolden-mcp"}):
            raise ValueError("invalid components")
        digest_data = []
        for row in components:
            if row.get("status") not in MCP_STATUSES:
                raise ValueError("invalid MCP state")
            if "reason" in row and row["reason"] not in MCP_REASONS:
                raise ValueError("invalid MCP reason")
            selected = {"component": row["component"], "status": row["status"]}
            # Hash selected bounded metadata to distinguish a new proposal while
            # keeping versions, plan IDs, and operator details out of evidence.
            for key in ("installed", "candidate", "plan_id", "classification", "reason"):
                if key in row:
                    if not isinstance(row[key], str) or len(row[key]) > 256:
                        raise ValueError("invalid MCP metadata")
                    if key != "plan_id":  # Re-preparing the same candidate is not a new finding.
                        selected[key] = row[key]
            digest_data.append(selected)
        digest_data.sort(key=lambda row: row["component"])
        approvals = sum(row["status"] == "approval_required" for row in components)
        errors = sum(row["status"] in {"failed", "uncertain", "blocked"} for row in components)
        stamp = generated.isoformat().replace("+00:00", "Z")
        provenance = {"source_report_generated_at": stamp, "source_digest": hashlib.sha256(
            json.dumps(digest_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        coverage_data = [{"component": row["component"], "status": row["status"]}
                         for row in digest_data if row["status"] in {"failed", "uncertain", "blocked"}]
        coverage = dict(provenance, source_digest=hashlib.sha256(json.dumps(
            coverage_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), count=errors)
        result = [make(source, "degraded" if approvals or errors else "healthy",
                       dict(provenance, candidate_count=approvals, count=errors)),
                  make(source + ".coverage", "degraded" if errors else "healthy", coverage)]
        for row in result:
            row["observed_at"] = stamp
            row["expires_at"] = (generated + timedelta(seconds=MCP_TTL)).isoformat().replace("+00:00", "Z")
        return result
    except (ValueError, KeyError, TypeError, OverflowError):
        return unavailable("unsupported_schema")


def semantic_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    # Only content-addressed reports can safely omit their changing timestamp.
    return {k: v for k, v in evidence.items() if k != "source_report_generated_at" or "source_digest" not in evidence}


def project(system: dict[str, Any], raw: dict[str, Any], source: str, now: datetime,
            check: Any) -> list[dict[str, Any]]:
    if source == "host-update-report":
        return project_host(system, raw, now, check)
    if source == "mcp-update-report":
        return project_mcp(system, raw, now, check)
    report = raw.get("maintenance_report")
    make = lambda key, state, evidence: check(system, key, state, evidence, now, updates=True)
    def unavailable(reason: str) -> list[dict[str, Any]]:
        # A failed/stale check is actionable separately from its last update result.
        return [make(source, "unknown", {"reason": reason}),
                make(source + ".coverage", "degraded", {"reason": reason})]
    try:
        if (not isinstance(report, dict) or report.get("schema_version") != 1
                or report.get("host") != system["target"] or report.get("mode") != "observe-only"):
            return unavailable("update_metadata_unavailable")
        generated = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError("timezone required")
        generated = generated.astimezone(timezone.utc)
        if not -30 <= (now - generated).total_seconds() <= TTL:
            return unavailable("stale")
        data = report["package_updates" if source == "package-update-report" else "docker_image_updates"]
        if not isinstance(data, dict):
            raise ValueError("report object required")
        if source == "package-update-report":
            state = data.get("status")
            if state not in {"current", "updates_available", "unknown", "stale", "error"}:
                raise ValueError("invalid package state")
            candidates = data.get("candidate_count")
            provenance = data.get("provenance", {})
            skipped = provenance.get("skipped_sources", 0) if isinstance(provenance, dict) else 1
            missing = int(state in {"unknown", "stale", "error"} or skipped != 0)
            digest_data = {k: data.get(k) for k in ("lane", "scope", "status", "candidate_count",
                          "security_count", "reboot_required", "candidates", "candidate_digest_sha256", "reason", "truncated")}
            coverage_data = {"missing": missing, "reason": data.get("reason"), "skipped": skipped}
        else:
            images = data.get("images")
            if not isinstance(images, list) or len(images) > 256:
                raise ValueError("bounded image list required")
            states = {"current", "update_available", "unknown", "untracked"}
            if any(not isinstance(x, dict) or x.get("state") not in states for x in images):
                raise ValueError("invalid image state")
            candidates = sum(x["state"] == "update_available" for x in images)
            missing = sum(x["state"] in {"unknown", "untracked"} for x in images)
            if not images:
                missing = 1
            digest_data = [{k: x.get(k) for k in ("container", "image", "tracked_tag", "state", "reason",
                           "local_manifest_digest", "remote_platform_digest")} for x in images]
            coverage_data = [x for x in digest_data if x.get("state") in {"unknown", "untracked"}]
        if candidates is not None and (type(candidates) is not int or not 0 <= candidates <= 10000000):
            raise ValueError("invalid count")
        digest = hashlib.sha256(json.dumps(digest_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        provenance = {"source_report_generated_at": generated.isoformat().replace("+00:00", "Z"),
                      "source_digest": digest}
        coverage = dict(provenance, source_digest=hashlib.sha256(json.dumps(coverage_data, sort_keys=True).encode()).hexdigest())
        available = dict(provenance, candidate_count=candidates) if candidates is not None else dict(provenance, reason="update_metadata_unavailable")
        if source == "package-update-report":
            if type(data.get("security_count")) is int and 0 <= data["security_count"] <= 10000000:
                available["security_count"] = data["security_count"]
            if data.get("scope") in {"installed_packages", "package_catalog"}:
                available["update_scope"] = data["scope"]
        result = [make(source, "unknown" if candidates is None or (source == "package-update-report"
                       and (state not in {"current", "updates_available"} or (missing and not candidates)))
                       else "degraded" if candidates else "healthy",
                       available),
                  make(source + ".coverage", "degraded" if missing else "healthy",
                       dict(coverage, count=missing))]
        if source == "package-update-report":
            reboot = data.get("reboot_required")
            reboot_provenance = dict(provenance, source_digest=hashlib.sha256(json.dumps(
                [reboot, data.get("reboot_package_count")]).encode()).hexdigest())
            result.append(make(source + ".reboot-required", "unknown" if type(reboot) is not bool
                               else "degraded" if reboot else "healthy",
                               dict(reboot_provenance, available=reboot) if type(reboot) is bool
                               else dict(reboot_provenance, reason="update_metadata_unavailable")))
        for row in result:
            row["observed_at"] = provenance["source_report_generated_at"]
            row["expires_at"] = (generated + timedelta(seconds=TTL)).isoformat().replace("+00:00", "Z")
        return result
    except (ValueError, KeyError, TypeError, OverflowError):
        return unavailable("unsupported_schema")
