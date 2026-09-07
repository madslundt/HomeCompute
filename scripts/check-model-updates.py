#!/usr/bin/env python3
"""Review-only upstream update monitor for HomeCompute."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from model_update_selection import (
    benchmark_classifications as _benchmark_classifications,
    validate_selection,
)
from update_check_http import (
    MAX_DOCUMENT_BYTES,
    SourceError,
    ValidationError,
    fetch_json,
    resolved_addresses,
    validate_project_url,
    validate_request_url,
)

SCHEMA_VERSION = 1
ALLOWED_KINDS = {
    "huggingface_model",
    "huggingface_search",
    "github_file",
    "github_release",
}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@/-]{0,511}$")

HASH_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")

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





def _read_json(path: Path, name: str, *, optional: bool = False) -> Any:
    if optional and not path.exists():
        return None
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise ValidationError(f"{name} must be an existing regular file, not a symbolic link: {path}")
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValidationError(f"{name} exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"{name} is not readable JSON: {error}") from error


def _validate_output_path(path: Path, name: str) -> None:
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise ValidationError(f"{name} must be a regular file, not a symbolic link: {path}")
    parent = path.parent
    if not parent.exists() or not parent.is_dir() or parent.is_symlink():
        raise ValidationError(f"{name} parent must be an existing real directory: {parent}")




def load_watchlist(path: Path, resolver: Callable[[str], list[str]]) -> dict[str, Any]:
    document = _require_object(_read_json(path, "watchlist"), "watchlist")
    _check_keys(document, "watchlist", {"schema_version", "project", "policy", "sources"})
    if document["schema_version"] != SCHEMA_VERSION or document["project"] != "HomeCompute":
        raise ValidationError("watchlist has an unsupported schema or project")
    policy = _require_object(document["policy"], "watchlist.policy")
    _check_keys(policy, "watchlist.policy", {"mode", "automatic_upgrade", "reason"})
    if policy["mode"] != "review-only" or policy["automatic_upgrade"] is not False:
        raise ValidationError("watchlist policy must be review-only with automatic upgrades disabled")
    _require_string(policy["reason"], "watchlist.policy.reason", 1000)
    sources = document["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 100:
        raise ValidationError("watchlist.sources must contain between 1 and 100 sources")
    seen_ids: set[str] = set()
    seen_benchmark_ids: set[str] = set()
    for index, item in enumerate(sources):
        source = _require_object(item, f"watchlist.sources[{index}]")
        _check_keys(
            source,
            f"watchlist.sources[{index}]",
            {"id", "kind", "label", "role", "request_url", "project_url"},
            {"benchmark_candidate_ids", "benchmark_artifact_source"},
        )
        source_id = _require_string(source["id"], f"source {index} id", 128)
        if not ID_PATTERN.fullmatch(source_id) or source_id in seen_ids:
            raise ValidationError(f"source id is invalid or duplicated: {source_id}")
        seen_ids.add(source_id)
        if source["kind"] not in ALLOWED_KINDS:
            raise ValidationError(f"source {source_id} has an unsupported kind")
        _require_string(source["label"], f"source {source_id} label", 256)
        _require_string(source["role"], f"source {source_id} role", 512)
        request_url = _require_string(source["request_url"], f"source {source_id} request_url", 2000)
        project_url = _require_string(source["project_url"], f"source {source_id} project_url", 2000)
        try:
            validate_request_url(request_url, source["kind"], resolver)
        except SourceError:
            # Transient DNS failures belong in the run report, not schema validation.
            pass
        validate_project_url(project_url)
        benchmark_ids = source.get("benchmark_candidate_ids", [])
        has_benchmark_source = "benchmark_artifact_source" in source
        if (
            not isinstance(benchmark_ids, list)
            or len(benchmark_ids) > 32
            or ("benchmark_candidate_ids" in source and source["kind"] != "huggingface_model")
            or bool(benchmark_ids) != has_benchmark_source
        ):
            raise ValidationError(
                f"source {source_id} must pair benchmark_candidate_ids with benchmark_artifact_source"
            )
        if has_benchmark_source:
            _require_string(
                source["benchmark_artifact_source"],
                f"source {source_id} benchmark_artifact_source",
                1000,
            )
        for candidate_id in benchmark_ids:
            candidate_id = _require_string(candidate_id, f"source {source_id} benchmark candidate id", 128)
            if not ID_PATTERN.fullmatch(candidate_id) or candidate_id in seen_benchmark_ids:
                raise ValidationError(f"benchmark candidate id is duplicated: {candidate_id}")
            seen_benchmark_ids.add(candidate_id)
    return document


def load_state(path: Path) -> tuple[dict[str, Any], bool]:
    document = _read_json(path, "state", optional=True)
    if document is None:
        return {"schema_version": SCHEMA_VERSION, "sources": {}}, True
    state = _require_object(document, "state")
    _check_keys(state, "state", {"schema_version", "sources"})
    if state["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("state has an unsupported schema_version")
    sources = _require_object(state["sources"], "state.sources")
    for source_id, marker in sources.items():
        if not ID_PATTERN.fullmatch(source_id) or not isinstance(marker, dict):
            raise ValidationError("state contains an invalid source entry")
        _check_keys(marker, f"state source {source_id}", {"marker", "observed_version", "last_success_at"})
        revision_fields = ("marker", "observed_version")
        if any(not isinstance(marker[field], str) or not REVISION_PATTERN.fullmatch(marker[field]) for field in revision_fields):
            raise ValidationError(f"state source {source_id} has invalid revision metadata")
        last_success = _require_string(marker["last_success_at"], f"state source {source_id} last_success_at", 64)
        try:
            parsed_time = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValidationError(f"state source {source_id} has an invalid last_success_at") from error
        if not last_success.endswith("Z") or parsed_time.utcoffset() is None:
            raise ValidationError(f"state source {source_id} has an invalid last_success_at")
    return state, False


def load_pins(path: Path | None, source_ids: set[str]) -> dict[str, Any]:
    if path is None:
        return {"pins": {}, "active_source_id": None}
    document = _require_object(_read_json(path, "pins"), "pins")
    _check_keys(document, "pins", {"schema_version", "pins"}, {"active_source_id"})
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("pins has an unsupported schema_version")
    pins = _require_object(document["pins"], "pins.pins")
    for source_id, revision in pins.items():
        if source_id not in source_ids or not isinstance(revision, str) or not REVISION_PATTERN.fullmatch(revision):
            raise ValidationError(f"pins contains an unknown source or invalid revision: {source_id}")
    active = document.get("active_source_id")
    if active is not None and active not in source_ids:
        raise ValidationError("pins.active_source_id must identify a watched source")
    return {"pins": pins, "active_source_id": active}


def load_selection(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return validate_selection(_read_json(path, "benchmark selection"))


def normalize(source: dict[str, Any], payload: Any) -> tuple[str, str]:
    kind = source["kind"]
    if kind == "huggingface_model":
        item = _require_object(payload, "Hugging Face model response")
        marker = item.get("sha")
        if not isinstance(marker, str) or not HASH_PATTERN.fullmatch(marker):
            raise SourceError("invalid_response", "Hugging Face model response has no valid revision")
        return marker, marker
    if kind == "huggingface_search":
        if not isinstance(payload, list) or len(payload) > 100:
            raise SourceError("invalid_response", "Hugging Face search response is invalid")
        entries = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise SourceError("invalid_response", "Hugging Face search entry is invalid")
            sha = item.get("sha") if isinstance(item.get("sha"), str) else None
            modified = item.get("lastModified") if isinstance(item.get("lastModified"), str) else None
            if sha is None and modified is None:
                raise SourceError("invalid_response", "Hugging Face search entry has no revision metadata")
            entries.append({"id": item["id"], "sha": sha, "last_modified": modified})
        encoded = json.dumps(sorted(entries, key=lambda entry: entry["id"]), sort_keys=True, separators=(",", ":")).encode()
        marker = hashlib.sha256(encoded).hexdigest()
        return marker, marker
    if kind == "github_file":
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise SourceError("invalid_response", "GitHub commit response is invalid")
        marker = payload[0].get("sha")
        if not isinstance(marker, str) or not HASH_PATTERN.fullmatch(marker):
            raise SourceError("invalid_response", "GitHub commit response has no valid revision")
        return marker, marker
    item = _require_object(payload, "GitHub release response")
    marker = item.get("tag_name")
    if not isinstance(marker, str) or not REVISION_PATTERN.fullmatch(marker):
        raise SourceError("invalid_response", "GitHub release response has no valid tag")
    return marker, marker


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    data = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def execute(watchlist_path: Path, state_path: Path, report_path: Path, pins_path: Path | None = None, selection_path: Path | None = None, *, fetcher: Callable[[str, str, Callable[[str], list[str]]], Any] = fetch_json, resolver: Callable[[str], list[str]] = resolved_addresses, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    paths = [watchlist_path, state_path, report_path] + [path for path in (pins_path, selection_path) if path is not None]
    if len({path.resolve(strict=False) for path in paths}) != len(paths):
        raise ValidationError("watchlist, state, report, pins, and selection paths must be distinct")
    _validate_output_path(state_path, "state")
    _validate_output_path(report_path, "report")
    watchlist = load_watchlist(watchlist_path, resolver)
    state, first_run = load_state(state_path)
    sources = watchlist["sources"]
    source_ids = {source["id"] for source in sources}
    pins = load_pins(pins_path, source_ids)
    if pins["active_source_id"] is not None:
        active = next(source for source in sources if source["id"] == pins["active_source_id"])
        if active["kind"] != "huggingface_model" or pins["active_source_id"] not in pins["pins"]:
            raise ValidationError("pins.active_source_id must identify a pinned Hugging Face model source")
    selection = load_selection(selection_path)
    timestamp = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    next_state = {"schema_version": SCHEMA_VERSION, "sources": dict(state["sources"])}
    baselines: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    pin_drift: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    observed_revisions: dict[str, str] = {}
    for source in sources:
        source_id = source["id"]
        try:
            payload = fetcher(source["request_url"], source["kind"], resolver)
            marker, observed = normalize(source, payload)
        except SourceError as error:
            errors.append({"source_id": source_id, "label": source["label"], "project_url": source["project_url"], "code": error.code, "message": error.message})
            continue
        except (ValidationError, TypeError, ValueError, KeyError) as error:
            errors.append({"source_id": source_id, "label": source["label"], "project_url": source["project_url"], "code": "invalid_response", "message": str(error)})
            continue
        observed_revisions[source_id] = observed
        previous = state["sources"].get(source_id)
        if previous is None:
            baselines.append({"source_id": source_id, "marker": marker})
        elif previous["marker"] != marker:
            changes.append({"source_id": source_id, "label": source["label"], "project_url": source["project_url"], "previous_marker": previous["marker"], "current_marker": marker})
        installed = pins["pins"].get(source_id)
        if installed is not None and installed != observed:
            pin_drift.append({"source_id": source_id, "label": source["label"], "project_url": source["project_url"], "installed_revision": installed, "observed_revision": observed})
        next_state["sources"][source_id] = {"marker": marker, "observed_version": observed, "last_success_at": timestamp}
    classifications, benchmark_evidence = _benchmark_classifications(
        sources, pins, selection, observed_revisions
    )
    outperforming = sum(row["classification"] == "outperforms_active" for row in classifications)
    attention = bool(changes or pin_drift or errors or outperforming)
    report = {
        "schema_version": SCHEMA_VERSION,
        "document_type": "model_update_report",
        "generated_at": timestamp,
        "mode": "review-only",
        "automatic_download": False,
        "automatic_promotion": False,
        "status": "attention" if attention else ("baseline" if first_run else "quiet"),
        "baseline_created": first_run,
        "summary": {
            "baselined": len(baselines),
            "changed": len(changes),
            "pin_drift": len(pin_drift),
            "source_errors": len(errors),
            "unbenchmarked": sum(row["classification"] == "unbenchmarked" for row in classifications),
            "not_better": sum(row["classification"] == "not_better" for row in classifications),
            "outperforms_active": outperforming,
        },
        "baselines": baselines,
        "changes": changes,
        "pin_drift": pin_drift,
        "source_errors": errors,
        "benchmark_evidence": benchmark_evidence,
        "candidate_classifications": classifications,
    }
    _atomic_write(report_path, report)
    _atomic_write(state_path, next_state)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchlist", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--pins", type=Path)
    parser.add_argument("--selection", type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = execute(arguments.watchlist, arguments.state, arguments.report, arguments.pins, arguments.selection)
    except ValidationError as error:
        print(f"update-check: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"update-check: filesystem operation failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "report": str(arguments.report), "source_errors": report["summary"]["source_errors"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
