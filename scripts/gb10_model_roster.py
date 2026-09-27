#!/usr/bin/env python3
"""Validate immutable model-roster structure and deployment invariants.

The roster is data. This validator deliberately does not select or require a
particular model, runtime, parser, speech stack, or fixed number of artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REVISION = re.compile(r"^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")


class RosterError(ValueError):
    """The roster violates a platform invariant."""


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RosterError(f"{name} must be an object")
    return value


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise RosterError(f"{name} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise RosterError(f"{name} must not contain duplicates")
    if not allow_empty and not value:
        raise RosterError(f"{name} must not be empty")
    return value


def _check_no_secrets(value: Any, name: str = "roster") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in ("api_key", "password", "secret", "access_token")):
                raise RosterError(f"{name} contains secret-bearing field {key!r}")
            _check_no_secrets(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_no_secrets(child, f"{name}[{index}]")


def _model(entry: Any, name: str) -> dict[str, Any]:
    model = _object(entry, name)
    model_id = model.get("model_id")
    if not isinstance(model_id, str) or "/" not in model_id:
        raise RosterError(f"{name}.model_id must be an upstream repository ID")
    revision = model.get("revision")
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise RosterError(f"{name}.revision must be an immutable commit or sha256 digest")
    if not isinstance(model.get("license_id"), str) or not model["license_id"]:
        raise RosterError(f"{name}.license_id must be non-empty")
    for field in ("runtime_profile", "runtime_profiles"):
        if field not in model:
            continue
        profiles = model[field]
        if field == "runtime_profile":
            profiles = {"default": profiles}
        profiles = _object(profiles, f"{name}.{field}")
        if not profiles:
            raise RosterError(f"{name}.{field} must not be empty")
        for profile_name, raw_profile in profiles.items():
            profile = _object(raw_profile, f"{name}.{field}.{profile_name}")
            if not isinstance(profile.get("runtime"), str) or not profile["runtime"]:
                raise RosterError(f"{name}.{field}.{profile_name}.runtime must be non-empty")
            for optional in ("tool_call_parser", "reasoning_parser", "status"):
                if optional in profile and not isinstance(profile[optional], str):
                    raise RosterError(f"{name}.{field}.{profile_name}.{optional} must be a string")
    return model


def validate_roster(roster: dict[str, Any]) -> None:
    _check_no_secrets(roster)
    version = roster.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise RosterError("schema_version must be a positive integer")
    if not isinstance(roster.get("roster_version"), str) or not roster["roster_version"]:
        raise RosterError("roster_version must be non-empty")

    hardware = _object(roster.get("hardware"), "hardware")
    if not isinstance(hardware.get("accelerator"), str) or not hardware["accelerator"]:
        raise RosterError("hardware.accelerator must be non-empty")
    memory = hardware.get("unified_memory_gib")
    resident_limit = hardware.get("resident_text_model_limit")
    retained_limit = hardware.get("retained_text_model_limit")
    if not isinstance(memory, (int, float)) or isinstance(memory, bool) or memory <= 0:
        raise RosterError("hardware.unified_memory_gib must be positive")
    if not isinstance(resident_limit, int) or resident_limit < 1:
        raise RosterError("hardware.resident_text_model_limit must be a positive integer")
    if not isinstance(retained_limit, int) or retained_limit < resident_limit:
        raise RosterError("hardware.retained_text_model_limit must be >= resident_text_model_limit")

    sections = ("text_models", "services", "supporting_services")
    revisions_by_model: dict[str, str] = {}
    models_by_section: dict[str, dict[str, dict[str, Any]]] = {}
    for section_name in sections:
        section = _object(roster.get(section_name), section_name)
        models_by_section[section_name] = {}
        for name, raw_model in section.items():
            if not isinstance(name, str) or not name:
                raise RosterError(f"{section_name} contains an empty artifact name")
            model = _model(raw_model, f"{section_name}.{name}")
            model_id = model["model_id"]
            revision = model["revision"]
            existing_revision = revisions_by_model.get(model_id)
            if existing_revision is not None and existing_revision != revision:
                raise RosterError(f"{model_id} is recorded with conflicting immutable revisions")
            revisions_by_model[model_id] = revision
            models_by_section[section_name][name] = model

    if not models_by_section["text_models"]:
        raise RosterError("text_models must contain at least one artifact")

    excluded = _strings(roster.get("excluded_models"), "excluded_models", allow_empty=True)
    active_ids = {model["model_id"] for models in models_by_section.values() for model in models.values()}
    if active_ids.intersection(excluded):
        raise RosterError("an excluded model cannot also appear in the roster")

    profiles = _object(roster.get("operating_profiles"), "operating_profiles")
    for name, raw_profile in profiles.items():
        profile = _object(raw_profile, f"operating_profiles.{name}")
        model_ref = profile.get("text_model")
        if model_ref is not None and model_ref not in models_by_section["text_models"]:
            raise RosterError(f"operating_profiles.{name}.text_model references an unknown artifact")
        for field in ("stop_text_model_first", "restart_text_model_after"):
            target = profile.get(field)
            if target is not None and target not in models_by_section["text_models"]:
                raise RosterError(f"operating_profiles.{name}.{field} references an unknown artifact")
        if "dynamic_router_activation" in profile and not isinstance(profile["dynamic_router_activation"], bool):
            raise RosterError(f"operating_profiles.{name}.dynamic_router_activation must be boolean")

    _strings(roster.get("deployment_order"), "deployment_order", allow_empty=True)


def load_roster(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RosterError(f"cannot read {path}: {exc}") from exc
    return _object(value, str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        roster = load_roster(args.roster)
        validate_roster(roster)
    except RosterError as exc:
        print(f"gb10-model-roster: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"roster_version": roster["roster_version"], "valid": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
