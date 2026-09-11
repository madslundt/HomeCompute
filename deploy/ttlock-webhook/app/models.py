from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = "ttlock"
    event_type: str
    lock_id: str
    user_id: str | None = None
    user_name: str | None = None
    timestamp: datetime
    event_id: str
    raw_event_type: int
    success: bool
