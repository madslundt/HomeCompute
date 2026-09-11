import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from .models import NormalizedEvent


class TTLockRecord(BaseModel):
    """Documented TTLock Cloud API v3 lock-record fields.

    Unknown fields are ignored deliberately so undocumented, non-sensitive callback
    metadata does not break delivery. Only normalized allow-listed fields leave the
    gateway; keyboardPwd and other credentials can never reach Home Assistant.
    """

    model_config = ConfigDict(extra="ignore")

    lock_id: int | str = Field(validation_alias=AliasChoices("lockId", "lock_id"))
    record_type: int = Field(validation_alias=AliasChoices("recordType", "record_type"))
    success: bool = True
    username: str | None = Field(
        default=None, validation_alias=AliasChoices("username", "userName", "user_name")
    )
    user_id: int | str | None = Field(
        default=None, validation_alias=AliasChoices("openid", "userId", "user_id")
    )
    lock_date: int = Field(
        validation_alias=AliasChoices("lockDate", "lock_date", "date", "serverDate")
    )
    record_id: int | str | None = Field(
        default=None,
        validation_alias=AliasChoices("recordId", "record_id", "id", "eventId", "event_id"),
    )

    @field_validator("lock_id")
    @classmethod
    def nonempty_lock_id(cls, value: int | str) -> int | str:
        if not str(value).strip():
            raise ValueError("lockId must not be empty")
        return value

    @field_validator("lock_date")
    @classmethod
    def plausible_timestamp(cls, value: int) -> int:
        seconds = value / 1000 if value > 10_000_000_000 else value
        if not 946_684_800 <= seconds <= 4_102_444_800:
            raise ValueError("lockDate must be a Unix timestamp between 2000 and 2100")
        return value


RECORD_TYPES = {
    1: "app_unlock",
    3: "gateway_unlock",
    4: "keypad_unlock",
    7: "card_unlock",
    8: "fingerprint_unlock",
    9: "wristband_unlock",
    10: "mechanical_key_unlock",
    11: "bluetooth_unlock",
    12: "gateway_unlock",
    29: "unexpected_unlock",
    30: "door_closed",
    31: "door_open",
    32: "inside_unlock",
    33: "fingerprint_lock",
    34: "keypad_lock",
    35: "card_lock",
    36: "mechanical_key_lock",
    37: "remote_control",
    44: "tamper_alert",
    45: "auto_lock",
    46: "unlock",
    47: "locked",
    48: "unlock_failed",
}


def normalize(record: TTLockRecord) -> NormalizedEvent:
    success = bool(record.success)
    event_type = RECORD_TYPES.get(record.record_type, "unknown")
    if not success and ("unlock" in event_type or event_type == "unknown"):
        event_type = "unlock_failed"

    seconds = record.lock_date / 1000 if record.lock_date > 10_000_000_000 else record.lock_date
    timestamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
    stable = {
        "lock_id": str(record.lock_id),
        "record_type": record.record_type,
        "success": success,
        "timestamp": timestamp.isoformat(),
        "user_id": None if record.user_id is None else str(record.user_id),
    }
    event_id = (
        f"ttlock:{record.record_id}"
        if record.record_id is not None
        else "derived:" + hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return NormalizedEvent(
        event_type=event_type,
        lock_id=str(record.lock_id),
        user_id=None if record.user_id is None else str(record.user_id),
        user_name=record.username,
        timestamp=timestamp,
        event_id=event_id,
        raw_event_type=record.record_type,
        success=success,
    )


def parse_payload(payload: Any) -> TTLockRecord:
    # Some callback products wrap the record. This compatibility is structural,
    # not an assertion that TTLock officially uses a wrapper.
    if isinstance(payload, dict) and isinstance(payload.get("record"), dict):
        payload = payload["record"]
    return TTLockRecord.model_validate(payload)
