"""Validate benchmark selections and bind them to watched model revisions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from update_check_http import ValidationError

SCHEMA_VERSION = 1
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@/-]{0,511}$")


def _require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must be a JSON object")
    return value


def _require_string(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValidationError(f"{name} must be a non-empty string of at most {maximum} characters")
    return value


def _check_keys(value: dict[str, Any], name: str, required: set[str], optional: set[str] = set()) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise ValidationError(f"{name} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValidationError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")


def _require_number(value: Any, name: str, minimum: float = 0, maximum: float | None = None) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise ValidationError(f"{name} must be a finite number between {minimum} and {maximum}")
    return value


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_selection(selection: Any) -> dict[str, Any]:
    selection = _require_object(selection, "benchmark selection")
    required = {
        "schema_version", "document_type", "identity", "parameters",
        "candidates", "rankings", "outcome", "winner",
    }
    _check_keys(selection, "benchmark selection", required)
    if selection["schema_version"] != SCHEMA_VERSION or selection["document_type"] != "benchmark_selection":
        raise ValidationError("benchmark selection has an unsupported schema or document_type")

    identity = _require_object(selection["identity"], "benchmark selection identity")
    identity_fields = {
        "run_id", "benchmark_id", "benchmark_version", "plan_sha256",
        "release_id", "release_sha256", "tracks",
    }
    _check_keys(identity, "benchmark selection identity", identity_fields)
    for field in identity_fields - {"tracks"}:
        _require_string(identity[field], f"benchmark selection identity.{field}", 256)
    for field in ("run_id", "benchmark_id", "benchmark_version", "release_id"):
        if not REVISION_PATTERN.fullmatch(identity[field]):
            raise ValidationError(f"benchmark selection identity.{field} is invalid")
    tracks = identity["tracks"]
    if (
        not isinstance(tracks, list)
        or not 1 <= len(tracks) <= 32
        or not all(isinstance(track, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", track) for track in tracks)
    ):
        raise ValidationError("benchmark selection identity.tracks must contain 1 to 32 identifiers")
    for field in ("plan_sha256", "release_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", identity[field]):
            raise ValidationError(f"benchmark selection identity.{field} must be a SHA-256 digest")

    parameters = _require_object(selection["parameters"], "benchmark selection parameters")
    _check_keys(parameters, "benchmark selection parameters", {"minimum_quality", "minimum_objective_pass_rate"})
    minimum_quality = _require_number(parameters["minimum_quality"], "minimum_quality", maximum=100)
    minimum_pass_rate = _require_number(
        parameters["minimum_objective_pass_rate"],
        "minimum_objective_pass_rate",
        maximum=100,
    )
    candidates = selection["candidates"]
    rankings = selection["rankings"]
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 100 or not isinstance(rankings, list):
        raise ValidationError("benchmark selection candidates or rankings is invalid")

    by_id: dict[str, dict[str, Any]] = {}
    eligible: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        item = _require_object(candidate, f"benchmark selection candidate {index}")
        _check_keys(
            item,
            f"benchmark selection candidate {index}",
            {
                "candidate_id", "candidate_label", "eligible", "ineligibility_reasons",
                "evidence", "artifact", "rank",
            },
        )
        candidate_id = _require_string(item["candidate_id"], f"benchmark candidate {index} id", 128)
        _require_string(item["candidate_label"], f"benchmark candidate {candidate_id} label", 256)
        if candidate_id in by_id or not isinstance(item["eligible"], bool):
            raise ValidationError("benchmark selection candidate ids must be unique and eligibility boolean")
        by_id[candidate_id] = item

        artifact = _require_object(item["artifact"], f"benchmark candidate {candidate_id} artifact")
        artifact_fields = {
            "artifact_ref", "source", "revision", "runtime", "quantization", "artifact_sha256",
        }
        _check_keys(artifact, f"benchmark candidate {candidate_id} artifact", artifact_fields)
        artifact_ref = _require_string(
            artifact["artifact_ref"],
            f"benchmark candidate {candidate_id} artifact.artifact_ref",
            512,
        )
        for field, maximum in (
            ("source", 1000),
            ("revision", 512),
            ("runtime", 1000),
            ("quantization", 1000),
        ):
            _require_string(artifact[field], f"benchmark candidate {candidate_id} artifact.{field}", maximum)
        if not re.fullmatch(r"[0-9a-f]{64}", artifact["artifact_sha256"]):
            raise ValidationError(f"benchmark candidate {candidate_id} artifact_sha256 is invalid")
        retained_artifact = {
            "id": artifact_ref,
            "source": artifact["source"],
            "revision": artifact["revision"],
            "runtime": artifact["runtime"],
            "quantization": artifact["quantization"],
        }
        if artifact["artifact_sha256"] != _canonical_sha256(retained_artifact):
            raise ValidationError(f"benchmark candidate {candidate_id} artifact provenance digest is inconsistent")

        evidence = _require_object(item["evidence"], f"benchmark candidate {candidate_id} evidence")
        count_fields = {
            "expected_evaluations", "completed_evaluations", "expected_judgments",
            "completed_judgments", "fatal_judgments", "failed_objective_evaluations",
        }
        _check_keys(
            evidence,
            f"benchmark candidate {candidate_id} evidence",
            count_fields | {
                "rubric_total", "rubric_maximum", "quality",
                "objective_pass_rate", "median_duration_ms",
            },
        )
        if any(
            isinstance(evidence[field], bool)
            or not isinstance(evidence[field], int)
            or evidence[field] < 0
            for field in count_fields
        ):
            raise ValidationError(f"benchmark candidate {candidate_id} has invalid evidence counts")
        expected_evaluations = evidence["expected_evaluations"]
        if (
            expected_evaluations < 1
            or evidence["completed_evaluations"] > expected_evaluations
            or evidence["completed_judgments"] > evidence["expected_judgments"]
            or evidence["fatal_judgments"] > evidence["completed_judgments"]
            or evidence["failed_objective_evaluations"] > expected_evaluations
        ):
            raise ValidationError(f"benchmark candidate {candidate_id} has inconsistent evidence counts")
        rubric_total = _require_number(
            evidence["rubric_total"], f"benchmark candidate {candidate_id} evidence.rubric_total"
        )
        rubric_maximum = _require_number(
            evidence["rubric_maximum"], f"benchmark candidate {candidate_id} evidence.rubric_maximum"
        )
        if rubric_total > rubric_maximum:
            raise ValidationError(f"benchmark candidate {candidate_id} rubric evidence is inconsistent")
        quality = evidence["quality"]
        if quality is not None:
            _require_number(quality, f"benchmark candidate {candidate_id} evidence.quality", maximum=100)
        expected_quality = round(100 * rubric_total / rubric_maximum, 6) if rubric_maximum else None
        if (
            (quality is None) != (expected_quality is None)
            or (
                quality is not None
                and not math.isclose(quality, expected_quality, rel_tol=0, abs_tol=0.000001)
            )
        ):
            raise ValidationError(f"benchmark candidate {candidate_id} quality summary is inconsistent")
        pass_rate = _require_number(
            evidence["objective_pass_rate"],
            f"benchmark candidate {candidate_id} evidence.objective_pass_rate",
            maximum=100,
        )
        expected_pass_rate = round(
            100 * (expected_evaluations - evidence["failed_objective_evaluations"]) / expected_evaluations,
            6,
        )
        if not math.isclose(pass_rate, expected_pass_rate, rel_tol=0, abs_tol=0.000001):
            raise ValidationError(f"benchmark candidate {candidate_id} objective pass summary is inconsistent")
        median_duration = evidence["median_duration_ms"]
        if median_duration is not None:
            _require_number(
                median_duration,
                f"benchmark candidate {candidate_id} evidence.median_duration_ms",
            )
        if (median_duration is None) != (evidence["completed_evaluations"] == 0):
            raise ValidationError(f"benchmark candidate {candidate_id} latency summary is inconsistent")

        expected_reasons = []
        if evidence["completed_evaluations"] != expected_evaluations:
            expected_reasons.append("incomplete_trials")
        if evidence["completed_judgments"] != evidence["expected_judgments"]:
            expected_reasons.append("incomplete_judgments")
        if evidence["fatal_judgments"]:
            expected_reasons.append("fatal_judgment")
        if evidence["failed_objective_evaluations"]:
            expected_reasons.append("failed_objective_checks")
        if quality is not None and quality < minimum_quality:
            expected_reasons.append("below_minimum_quality")
        if pass_rate < minimum_pass_rate:
            expected_reasons.append("below_minimum_objective_pass_rate")
        if item["ineligibility_reasons"] != expected_reasons or item["eligible"] != (not expected_reasons):
            raise ValidationError(f"benchmark candidate {candidate_id} eligibility summary is inconsistent")
        if item["rank"] is not None and (
            isinstance(item["rank"], bool) or not isinstance(item["rank"], int) or item["rank"] < 1
        ):
            raise ValidationError(f"benchmark candidate {candidate_id} has an invalid rank")
        if item["eligible"]:
            if quality is None or median_duration is None:
                raise ValidationError(f"eligible benchmark candidate {candidate_id} lacks rank evidence")
            eligible.append(item)

    expected_order = sorted(
        eligible,
        key=lambda item: (
            -item["evidence"]["quality"],
            -item["evidence"]["objective_pass_rate"],
            item["evidence"]["median_duration_ms"],
            item["candidate_id"],
        ),
    )
    expected_rankings = [
        {
            "rank": rank,
            "candidate_id": candidate["candidate_id"],
            "candidate_label": candidate["candidate_label"],
            "quality": candidate["evidence"]["quality"],
            "objective_pass_rate": candidate["evidence"]["objective_pass_rate"],
            "median_duration_ms": candidate["evidence"]["median_duration_ms"],
            "artifact": candidate["artifact"],
        }
        for rank, candidate in enumerate(expected_order, 1)
    ]
    if rankings != expected_rankings:
        raise ValidationError("benchmark rankings do not match deterministic eligible-candidate order")
    expected_ranks = {ranking["candidate_id"]: ranking["rank"] for ranking in expected_rankings}
    if any(candidate["rank"] != expected_ranks.get(candidate["candidate_id"]) for candidate in candidates):
        raise ValidationError("candidate ranks do not match recomputed rankings")
    expected_outcome = "winner_selected" if rankings else "no_eligible_winner"
    expected_winner = rankings[0] if rankings else None
    if selection["outcome"] != expected_outcome or selection["winner"] != expected_winner:
        raise ValidationError("benchmark outcome or winner does not match recomputed rankings")
    return selection


def benchmark_classifications(
    sources: list[dict[str, Any]],
    pins: dict[str, Any],
    selection: dict[str, Any] | None,
    observed_revisions: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model_sources = [source for source in sources if source["kind"] == "huggingface_model"]
    sources_by_id = {source["id"]: source for source in model_sources}
    candidate_to_source = {
        candidate_id: source["id"]
        for source in model_sources
        for candidate_id in source.get("benchmark_candidate_ids", [])
    }
    selection_candidates = (
        {} if selection is None
        else {item["candidate_id"]: item for item in selection["candidates"]}
    )
    active_source_id = pins["active_source_id"]
    active_candidates = [
        selection_candidates[candidate_id]
        for candidate_id, source_id in candidate_to_source.items()
        if source_id == active_source_id and candidate_id in selection_candidates
    ]
    active_ranked = [candidate for candidate in active_candidates if candidate["rank"] is not None]
    winner_id = None if selection is None or selection["winner"] is None else selection["winner"]["candidate_id"]
    winner_source_id = candidate_to_source.get(winner_id)

    blocker = None
    if selection is None:
        blocker = "selection_not_provided"
    elif active_source_id is None:
        blocker = "active_source_not_configured"
    elif not active_candidates:
        blocker = "active_candidate_not_in_selection"
    elif not active_ranked:
        blocker = "active_candidate_not_ranked"
    else:
        active_source = sources_by_id[active_source_id]
        expected_source = active_source["benchmark_artifact_source"]
        installed_revision = pins["pins"][active_source_id]
        if any(candidate["artifact"]["source"] != expected_source for candidate in active_ranked):
            blocker = "active_artifact_source_mismatch"
        elif any(candidate["artifact"]["revision"] != installed_revision for candidate in active_ranked):
            blocker = "active_artifact_revision_mismatch"
        elif winner_id is None:
            blocker = "selection_has_no_eligible_winner"
        elif winner_source_id is None:
            blocker = "winner_not_mapped_to_watchlist_source"
        elif winner_source_id != active_source_id:
            winner_source = sources_by_id[winner_source_id]
            winner_artifact = selection_candidates[winner_id]["artifact"]
            if winner_artifact["source"] != winner_source["benchmark_artifact_source"]:
                blocker = "challenger_artifact_source_mismatch"
            elif winner_source_id not in observed_revisions:
                blocker = "challenger_observation_unavailable"
            elif winner_artifact["revision"] != observed_revisions[winner_source_id]:
                blocker = "challenger_artifact_revision_mismatch"

    rows = []
    for source in model_sources:
        matches = [
            candidate_id
            for candidate_id in source.get("benchmark_candidate_ids", [])
            if candidate_id in selection_candidates
        ]
        ranked = [
            candidate_id
            for candidate_id in matches
            if selection_candidates[candidate_id]["rank"] is not None
        ]
        classification = "unbenchmarked"
        reason = "selection_not_provided" if selection is None else "candidate_not_in_selection"
        if matches:
            if blocker is not None:
                reason = blocker
            else:
                classification = "not_better"
                reason = "not_selected_over_active"
                if winner_source_id == source["id"] and winner_source_id != active_source_id:
                    classification = "outperforms_active"
                    reason = "eligible_winner_with_bound_artifact"
        rows.append({
            "source_id": source["id"],
            "classification": classification,
            "reason": reason,
            "benchmark_candidate_ids": sorted(matches),
            "best_rank": min(
                (selection_candidates[candidate_id]["rank"] for candidate_id in ranked),
                default=None,
            ),
            "promotion_allowed": False,
        })
    evidence = {
        "provided": selection is not None,
        "active_source_id": active_source_id,
        "comparison_status": blocker or "artifact_revisions_verified",
        "automatic_promotion": False,
    }
    if selection is not None:
        evidence["identity"] = selection["identity"]
        evidence["outcome"] = selection["outcome"]
    return rows, evidence
