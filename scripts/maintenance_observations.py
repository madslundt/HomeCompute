"""Finite update report projection; package names and registry data stay outside chat."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

SOURCES = {"package-update-report", "docker-image-report"}
TTL = 129600  # A missed daily collection becomes stale after 36 hours.


def semantic_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    # Only content-addressed reports can safely omit their changing timestamp.
    return {k: v for k, v in evidence.items() if k != "source_report_generated_at" or "source_digest" not in evidence}


def project(system: dict[str, Any], raw: dict[str, Any], source: str, now: datetime,
            check: Any) -> list[dict[str, Any]]:
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
