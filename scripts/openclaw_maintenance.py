"""Finite Homepage check requests and root-published status; no execution authority."""
from __future__ import annotations

import json
from datetime import datetime
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

REQUEST = Path("/var/lib/homecompute-openclaw/maintenance-requests/homepage.check")
STATUS = Path("/var/lib/homecompute-homepage-maintenance-public/status.json")
IMAGE = re.compile(r"ghcr\.io/gethomepage/homepage(?::v?\d+\.\d+\.\d+)?@sha256:[a-f0-9]{64}")
VERSION = re.compile(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?")
STATES = {"checking", "prepared", "approved", "attempt_started", "recreate_started", "succeeded",
          "rolled_back", "failed_before_recreate", "reconciliation_required", "up_to_date", "unavailable",
          "denied", "manual_review_required", "rollback_started"}
FIELDS = {"schema_version", "checked_at", "status", "current_image", "current_version", "candidate_image",
          "candidate_version", "release_notes", "proposal_id", "proposal_hash", "expires_at", "approval_required", "error"}
ERRORS = {"maintenance inputs unavailable", "check unavailable", "operator review required",
          "an uncertain previous attempt requires human reconciliation", "no reviewed candidate", "candidate unavailable",
          "Check or operation failed; trusted operator review required."}


def finite(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def timestamp(value: float) -> str:
    try:
        return datetime.fromtimestamp(value, ZoneInfo("Europe/Copenhagen")).strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, OSError, OverflowError) as error:
        raise ValueError("invalid maintenance timestamp") from error


def validate(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) - FIELDS
            or not {"schema_version", "checked_at", "status", "approval_required"} <= set(value)
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not finite(value["checked_at"]) or value["status"] not in STATES
            or value["approval_required"] is not True):
        raise ValueError("invalid maintenance status")
    for key in ("current_image", "candidate_image"):
        if key in value and value[key] is not None and (not isinstance(value[key], str) or not IMAGE.fullmatch(value[key])):
            raise ValueError("invalid Homepage image")
    for key in ("current_version", "candidate_version"):
        if key in value and value[key] is not None and (not isinstance(value[key], str)
                or not (VERSION.fullmatch(value[key]) or key == "current_version" and value[key] == "unknown")):
            raise ValueError("invalid Homepage version")
    for key, pattern in (("proposal_id", r"[a-f0-9]{32}"), ("proposal_hash", r"[a-f0-9]{64}")):
        if key in value and value[key] is not None and (not isinstance(value[key], str) or not re.fullmatch(pattern, value[key])):
            raise ValueError("invalid proposal identity")
    if value.get("expires_at") is not None and not finite(value["expires_at"]):
        raise ValueError("invalid proposal expiry")
    if value.get("release_notes") is not None and (not isinstance(value["release_notes"], str)
            or not re.fullmatch(r"https://github\.com/gethomepage/homepage/releases(?:/tag/v?\d+\.\d+\.\d+)?", value["release_notes"])):
        raise ValueError("invalid release notes")
    if "error" in value and value["error"] is not None and value["error"] not in ERRORS:
        raise ValueError("untrusted maintenance error")
    timestamp(value["checked_at"])
    if value.get("expires_at") is not None:
        timestamp(value["expires_at"])
    return value


class MaintenanceChat:
    def __init__(self, request_file: Path = REQUEST, status_file: Path = STATUS,
                 clock: Callable[[], float] = time.time, uid: int | None = None, status_uid: int = 0):
        self.request_file, self.status_file, self.clock = request_file, status_file, clock
        self.uid, self.status_uid = os.getuid() if uid is None else uid, status_uid
        self.last_request: float | None = None
        self.request_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        fd = os.open(self.status_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.status_uid or info.st_mode & 0o022 or info.st_nlink != 1:
                raise ValueError("maintenance status requires a trusted regular file")
            data = os.read(fd, 16385)
            if len(data) > 16384:
                raise ValueError("maintenance status exceeds budget")
            return validate(json.loads(data))
        finally:
            os.close(fd)

    def request_check(self) -> bool:
        with self.request_lock:
            return self._request_check()

    def _request_check(self) -> bool:
        now = self.clock()
        if not finite(now):
            raise ValueError("invalid clock")
        if self.last_request is not None and now - self.last_request < 30:
            return False
        fd = os.open(self.request_file, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 0):
                raise ValueError("maintenance request requires an existing private empty file")
            os.utime(fd, None)
            self.last_request = now
            return True
        finally:
            os.close(fd)

    @staticmethod
    def render(value: dict[str, Any]) -> str:
        lines = [f"Homepage: {value['status']}. Last report: {timestamp(value['checked_at'])}."]
        for label, version, image in (("Current", "current_version", "current_image"),
                                      ("Candidate", "candidate_version", "candidate_image")):
            if value.get(image):
                lines.append(label + ": " + str(value.get(version) or "unknown") + " — " + value[image])
        if value.get("release_notes"):
            lines.append("Release notes: " + value["release_notes"])
        if value.get("proposal_id"):
            lines.append("Proposal: " + value["proposal_id"] + "; reviewed digest: " + str(value.get("proposal_hash") or "unavailable"))
        if value.get("expires_at") is not None:
            lines.append("Proposal expires at " + timestamp(value["expires_at"]) + ".")
        if value.get("error"):
            lines.append("Check needs operator review.")
        lines.append("Production changes require separate human approval through the trusted operator. Chat cannot approve or deploy an image.")
        return "\n".join(lines)

    def respond(self, text: str) -> str | None:
        words = text.strip().split()
        if not words or words[0] not in {"/updates", "/update", "/maintenance"}:
            return None
        if words not in (["/updates", "homepage"], ["/update", "homepage"], ["/maintenance", "homepage"]):
            return "Use /updates homepage for a read-only check or /maintenance homepage for status. Only Homepage is enabled."
        prefix = ""
        if words[0] != "/maintenance":
            try:
                requested = self.request_check()
                prefix = ("Read-only Homepage check requested; the root collector runs asynchronously.\nPrevious status:\n" if requested
                          else "A Homepage check was already requested in the last 30 seconds.\nPrevious status:\n")
            except (OSError, ValueError):
                return "Homepage check request is unavailable; no production action ran. Contact the trusted operator."
        try:
            return prefix + self.render(self.status())
        except (OSError, ValueError, TypeError, KeyError):
            return prefix + "Trusted Homepage status is unavailable. No production change is authorized."
