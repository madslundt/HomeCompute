"""Pure approval classification for trusted infrastructure maintenance plans.

This classifies version metadata only. Executors must independently bind the
installed and candidate versions to package artifacts or immutable image IDs,
check freshness, backups, health and rollback, and enforce approval. Untrusted
assistant metadata must never itself authorize an effect.
"""
from __future__ import annotations

import re
from typing import Any, TypedDict

TARGETS = frozenset({"home-core", "home-spark", "home-assistant"})
SCHEMES = frozenset({"semver", "calendar", "nixos", "debian"})
COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}\Z")
NUMBER = r"(?:0|[1-9][0-9]*)"
SEMVER = re.compile(rf"v?({NUMBER})\.({NUMBER})\.({NUMBER})(\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z")
CALENDAR = re.compile(rf"([0-9]{{4}})\.([1-9]|1[0-2])\.({NUMBER})\Z")
NIXOS = re.compile(rf"(?:nixos-)?([0-9]{{2}})\.(05|11)(?:\.({NUMBER}))?\Z")
DEBIAN = re.compile(r"(?:(0|[1-9][0-9]*):)?([0-9]+(?:\.[0-9]+)*)(?:-([0-9][A-Za-z0-9.+]*))?\Z")


class Decision(TypedDict):
    classification: str
    approval_required: bool
    reason: str


def decision(classification: str, reason: str) -> Decision:
    return {"classification": classification,
            "approval_required": classification in {"major", "review"}, "reason": reason}


def version_text(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or value != value.strip():
        raise ValueError("bounded exact version text required")
    return value


def debian_part_compare(left: str, right: str) -> int:
    """Debian's non-digit/digit ordering for the supported stable syntax."""
    def order(char: str) -> int:
        if not char or char.isdigit():
            return 0
        return ord(char) if char.isalpha() else ord(char) + 256

    i = j = 0
    while i < len(left) or j < len(right):
        while (i < len(left) and not left[i].isdigit()) or (j < len(right) and not right[j].isdigit()):
            a = left[i] if i < len(left) else ""
            b = right[j] if j < len(right) else ""
            if order(a) != order(b):
                return 1 if order(a) > order(b) else -1
            if i < len(left):
                i += 1
            if j < len(right):
                j += 1
        while i < len(left) and left[i] == "0":
            i += 1
        while j < len(right) and right[j] == "0":
            j += 1
        a_start, b_start = i, j
        while i < len(left) and left[i].isdigit():
            i += 1
        while j < len(right) and right[j].isdigit():
            j += 1
        a, b = left[a_start:i], right[b_start:j]
        if len(a) != len(b):
            return 1 if len(a) > len(b) else -1
        if a != b:
            return 1 if a > b else -1
    return 0


def classify_version(installed: str, candidate: str, scheme: str) -> Decision:
    """Routine means an increasing stable version within a supported release."""
    installed, candidate = version_text(installed), version_text(candidate)
    if not isinstance(scheme, str) or scheme not in SCHEMES:
        raise ValueError("version scheme outside reviewed policy")
    parser = {"semver": SEMVER, "calendar": CALENDAR, "nixos": NIXOS, "debian": DEBIAN}[scheme]
    old, new = parser.fullmatch(installed), parser.fullmatch(candidate)
    if not old or not new:
        return decision("review", "unknown_or_prerelease")
    if scheme == "debian":
        if any(re.search(r"(?:alpha|beta|rc|pre|dev)", match[3] or "", re.I) for match in (old, new)):
            return decision("review", "unknown_or_prerelease")
        old_epoch, new_epoch = int(old[1] or "0"), int(new[1] or "0")
        upstream = debian_part_compare(new[2], old[2])
        revision = debian_part_compare(new[3] or "0", old[3] or "0")
        comparison = (new_epoch > old_epoch) - (new_epoch < old_epoch) if old_epoch != new_epoch else upstream or revision
        if comparison < 0:
            return decision("review", "downgrade")
        if comparison == 0:
            return decision("unchanged", "no_version_change")
        if new_epoch != old_epoch:
            return decision("major", "epoch_changed")
        if int(new[2].split(".")[0]) != int(old[2].split(".")[0]):
            return decision("major", "major_version_changed")
        return decision("routine", "same_major")
    old_numbers = tuple(int(old[index] or "0") for index in (1, 2, 3))
    new_numbers = tuple(int(new[index] or "0") for index in (1, 2, 3))
    if new_numbers < old_numbers:
        return decision("review", "downgrade")
    if new_numbers == old_numbers:
        if scheme == "semver" and old[4] != new[4]:
            return decision("review", "unordered_build_metadata")
        return decision("unchanged", "no_version_change")
    if scheme == "calendar" and new_numbers[:2] != old_numbers[:2]:
        return decision("major", "calendar_release_changed")
    if scheme == "nixos" and new_numbers[:2] != old_numbers[:2]:
        return decision("major", "release_channel_changed")
    if scheme == "semver":
        if new_numbers[0] != old_numbers[0]:
            return decision("major", "major_version_changed")
        if old_numbers[0] == 0 and new_numbers[1] != old_numbers[1]:
            return decision("major", "unstable_minor_changed")
    return decision("routine", "same_major" if scheme == "semver" else "same_release")


def classify_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """One exceptional component requires review of the entire exact plan."""
    if not isinstance(plan, dict) or set(plan) != {"operation", "target", "updates"}:
        raise ValueError("exact operation, target and update metadata required")
    operation, target, updates = plan["operation"], plan["target"], plan["updates"]
    if not isinstance(operation, str) or operation not in {"update", "fix"}:
        raise ValueError("operation outside update/fix policy")
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError("target outside reviewed infrastructure")
    if not isinstance(updates, list) or len(updates) > 4096 or operation == "update" and not updates:
        raise ValueError("bounded explicit component updates required")
    rows, components = [], set()
    for row in updates:
        if not isinstance(row, dict) or set(row) != {"component", "scheme", "installed", "candidate"}:
            raise ValueError("exact component version metadata required")
        component = row["component"]
        if not isinstance(component, str) or not COMPONENT.fullmatch(component) or component in components:
            raise ValueError("unique finite component identifier required")
        components.add(component)
        rows.append({**row, **classify_version(row["installed"], row["candidate"], row["scheme"])})
    if operation == "fix":
        result = decision("review", "fix_requires_approval")
    elif any(row["classification"] == "review" for row in rows):
        result = decision("review", "review_required")
    elif any(row["classification"] == "major" for row in rows):
        result = decision("major", "review_required")
    elif all(row["classification"] == "unchanged" for row in rows):
        result = decision("unchanged", "no_version_change")
    else:
        result = decision("routine", "all_same_release")
    return {"operation": operation, "target": target, **result, "updates": rows}
