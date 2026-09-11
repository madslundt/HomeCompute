import asyncio
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import Settings, get_settings
from .home_assistant import HomeAssistantUnavailable, forward_event, forward_raw_callback
from .ttlock import normalize, parse_payload


logger = logging.getLogger("ttlock_gateway")


class DedupeCache:
    def __init__(self, ttl: int) -> None:
        self.ttl = ttl
        self.completed: dict[str, float] = {}
        self.inflight: set[str] = set()
        self.lock = asyncio.Lock()

    async def reserve(self, event_id: str) -> str:
        now = time.monotonic()
        async with self.lock:
            self.completed = {key: expiry for key, expiry in self.completed.items() if expiry > now}
            if event_id in self.completed:
                return "completed"
            if event_id in self.inflight:
                return "inflight"
            self.inflight.add(event_id)
            return "reserved"

    async def finish(self, event_id: str, succeeded: bool) -> None:
        async with self.lock:
            self.inflight.discard(event_id)
            if succeeded:
                self.completed[event_id] = time.monotonic() + self.ttl


class RateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()
        self.calls = 0

    async def allow(self, client: str) -> bool:
        now = time.monotonic()
        async with self.lock:
            self.calls += 1
            if self.calls % 256 == 0:
                for key in list(self.requests):
                    bucket_to_clean = self.requests[key]
                    while bucket_to_clean and bucket_to_clean[0] <= now - 60:
                        bucket_to_clean.popleft()
                    if not bucket_to_clean:
                        del self.requests[key]
            if client not in self.requests and len(self.requests) >= 4096:
                client = "overflow"
            bucket = self.requests[client]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def client_identifier(request: Request) -> str:
    # The app is only bound to loopback, so this header can only arrive through
    # the trusted local Funnel proxy. Use the rightmost address it supplies.
    forwarded = request.headers.get("x-forwarded-for", "")
    for candidate in reversed(forwarded.split(",")):
        try:
            return str(ip_address(candidate.strip()))
        except ValueError:
            continue
    return request.client.host if request.client else "unknown"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
    )
    # HTTPX's INFO message includes the complete request URL. The HA webhook ID
    # is a credential, so dependency request logs must never inherit INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_event(level: int, message: str, **fields: object) -> None:
    logger.log(
        level,
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": logging.getLevelName(level).lower(),
                "message": message,
                **fields,
            },
            separators=(",", ":"),
        ),
    )


def decode_payloads(body: bytes, content_type: str) -> list[Any]:
    """Decode TTLock's JSON or form-encoded callback representation."""
    if content_type in {"application/json", "text/plain"}:
        return [json.loads(body)]

    if content_type == "application/x-www-form-urlencoded":
        fields = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=64,
        )
        encoded_batches = fields.get("records", [])
        if encoded_batches:
            payloads: list[Any] = []
            for encoded_batch in encoded_batches:
                batch = json.loads(encoded_batch)
                if not isinstance(batch, list):
                    raise ValueError("TTLock records must be a JSON array")
                payloads.extend(batch)
            return payloads
        # TTLock's console may send form fields without a records batch while
        # testing a callback URL. Acknowledge it without manufacturing an event.
        return []

    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="JSON or form data required",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()
    configure_logging(configured.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = configured
        app.state.dedupe = DedupeCache(configured.dedupe_ttl_seconds)
        app.state.rate_limiter = RateLimiter(configured.rate_limit_per_minute)
        yield

    app = FastAPI(
        title="TTLock webhook gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/webhooks/ttlock/{secret}", include_in_schema=False)
    async def ttlock_webhook(secret: str, request: Request) -> Response:
        started = time.monotonic()
        if not await request.app.state.rate_limiter.allow(client_identifier(request)):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
        expected = configured.ttlock_webhook_secret.get_secret_value()
        if not hmac.compare_digest(secret.encode(), expected.encode()):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
                if declared_length < 0:
                    raise ValueError
                if declared_length > configured.max_body_bytes:
                    raise HTTPException(status_code=413, detail="Request body too large")
            except ValueError as error:
                raise HTTPException(status_code=400, detail="Invalid Content-Length") from error
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > configured.max_body_bytes:
                raise HTTPException(status_code=413, detail="Request body too large")

        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if not chunks and content_type in {
            "",
            "application/json",
            "application/x-www-form-urlencoded",
            "text/plain",
        }:
            log_event(logging.INFO, "callback verification acknowledged")
            return JSONResponse({"status": "ok"})

        try:
            payloads = decode_payloads(bytes(chunks), content_type)
            records = [parse_payload(payload) for payload in payloads]
        except ValidationError as error:
            raise HTTPException(status_code=422, detail="Invalid TTLock event") from error
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="Malformed webhook body") from error

        if (
            records
            and configured.ha_ttlock_webhook_url is not None
            and content_type == "application/x-www-form-urlencoded"
        ):
            try:
                await forward_raw_callback(
                    bytes(chunks),
                    content_type,
                    str(configured.ha_ttlock_webhook_url),
                    configured.ha_timeout_seconds,
                )
            except HomeAssistantUnavailable as error:
                log_event(
                    logging.WARNING,
                    "TTLock integration relay failed",
                    record_count=len(records),
                    error=type(error).__name__,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Downstream temporarily unavailable",
                ) from error
            log_event(
                logging.INFO,
                "TTLock integration callback relayed",
                record_count=len(records),
            )

        for record in records:
            event = normalize(record)
            reservation = await request.app.state.dedupe.reserve(event.event_id)
            if reservation != "reserved":
                log_event(
                    logging.INFO,
                    "duplicate event ignored",
                    event_id=event.event_id,
                    event_type=event.event_type,
                    lock_id=event.lock_id,
                    state=reservation,
                )
                continue

            succeeded = False
            try:
                await forward_event(
                    event,
                    str(configured.ha_webhook_url),
                    configured.ha_timeout_seconds,
                )
                succeeded = True
            except HomeAssistantUnavailable as error:
                log_event(
                    logging.WARNING,
                    "event forwarding failed",
                    event_id=event.event_id,
                    event_type=event.event_type,
                    lock_id=event.lock_id,
                    error=type(error).__name__,
                )
                raise HTTPException(status_code=503, detail="Downstream temporarily unavailable") from error
            finally:
                await request.app.state.dedupe.finish(event.event_id, succeeded)

            log_event(
                logging.INFO,
                "event forwarded",
                event_id=event.event_id,
                event_type=event.event_type,
                lock_id=event.lock_id,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        return JSONResponse({"status": "ok"})

    return app
