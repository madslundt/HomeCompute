import logging
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.home_assistant import HomeAssistantUnavailable
from app.main import create_app


SECRET = "0b485b31995bf60eaa65c586f9095fc243c5574d9f112798b4050cc7fd821fed"
URL = f"/webhooks/ttlock/{SECRET}"


@pytest.fixture
def forward(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr("app.main.forward_event", mock)
    return mock


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        ttlock_webhook_secret=SecretStr(SECRET),
        ha_webhook_url="http://home-assistant.local:8123/api/webhook/separate-secret",
        max_body_bytes=1024,
        rate_limit_per_minute=30,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def event(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "recordId": 9001,
        "lockId": 123456,
        "recordType": 4,
        "success": 1,
        "username": "Mads",
        "openid": 789,
        "lockDate": 1_789_147_330_000,
        "keyboardPwd": "must-never-be-forwarded",
    }
    body.update(changes)
    return body


def test_health_does_not_depend_on_home_assistant(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_http_client_request_logging_cannot_expose_webhook_url(client: TestClient) -> None:
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_valid_event_is_normalized_and_forwarded_once(client: TestClient, forward: AsyncMock) -> None:
    response = client.post(URL, json=event())

    assert response.status_code == 200
    forward.assert_awaited_once()
    normalized = forward.await_args.args[0]
    assert normalized.event_type == "keypad_unlock"
    assert normalized.lock_id == "123456"
    assert normalized.user_id == "789"
    assert normalized.event_id == "ttlock:9001"
    assert "keyboardPwd" not in normalized.model_dump()


def test_wrong_secret_is_hidden(client: TestClient, forward: AsyncMock) -> None:
    assert client.post("/webhooks/ttlock/wrong", json=event()).status_code == 404
    forward.assert_not_awaited()


def test_get_on_webhook_is_rejected(client: TestClient) -> None:
    assert client.get(URL).status_code == 405


def test_malformed_json_is_rejected(client: TestClient, forward: AsyncMock) -> None:
    response = client.post(URL, content=b"{", headers={"content-type": "application/json"})
    assert response.status_code == 400
    forward.assert_not_awaited()


def test_missing_field_is_rejected(client: TestClient, forward: AsyncMock) -> None:
    assert client.post(URL, json={"lockId": 1}).status_code == 422
    forward.assert_not_awaited()


def test_wrong_content_type_is_rejected(client: TestClient, forward: AsyncMock) -> None:
    response = client.post(URL, content=b"{}", headers={"content-type": "application/octet-stream"})
    assert response.status_code == 415
    forward.assert_not_awaited()


def test_empty_form_callback_verification_is_acknowledged(
    client: TestClient, forward: AsyncMock
) -> None:
    response = client.post(
        URL,
        content=b"",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    forward.assert_not_awaited()


def test_form_callback_probe_fields_are_acknowledged_without_forwarding(
    client: TestClient, forward: AsyncMock
) -> None:
    response = client.post(URL, data={"test": "1"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    forward.assert_not_awaited()


def test_json_labeled_as_text_plain_is_accepted(
    client: TestClient, forward: AsyncMock
) -> None:
    response = client.post(
        URL,
        content=json.dumps(event(recordId=9004)),
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 200
    assert forward.await_args.args[0].event_id == "ttlock:9004"


def test_form_encoded_records_are_normalized_and_forwarded(
    client: TestClient, forward: AsyncMock
) -> None:
    response = client.post(
        URL,
        data={
            "lockId": "123456",
            "notifyType": "1",
            "records": json.dumps([event(recordId=9003)]),
            "admin": "redacted@example.invalid",
        },
    )

    assert response.status_code == 200
    forward.assert_awaited_once()
    normalized = forward.await_args.args[0]
    assert normalized.event_id == "ttlock:9003"
    assert normalized.event_type == "keypad_unlock"


def test_form_records_are_relayed_to_the_ttlock_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_forward = AsyncMock()
    raw_forward = AsyncMock()
    monkeypatch.setattr("app.main.forward_event", normalized_forward)
    monkeypatch.setattr("app.main.forward_raw_callback", raw_forward)
    settings = Settings(
        ttlock_webhook_secret=SecretStr(SECRET),
        ha_webhook_url="http://home-assistant.local/api/webhook/normalized-secret",
        ha_ttlock_webhook_url="http://home-assistant.local/api/webhook/integration-secret",
    )
    body = {
        "lockId": "123456",
        "notifyType": "1",
        "records": json.dumps([event(recordId=9005)]),
    }

    with TestClient(create_app(settings)) as relay_client:
        response = relay_client.post(URL, data=body)

    assert response.status_code == 200
    raw_forward.assert_awaited_once()
    assert raw_forward.await_args.args[1] == "application/x-www-form-urlencoded"
    assert "integration-secret" in raw_forward.await_args.args[2]
    normalized_forward.assert_awaited_once()


def test_raw_relay_failure_requests_a_ttlock_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_forward = AsyncMock()
    raw_forward = AsyncMock(side_effect=HomeAssistantUnavailable("timeout"))
    monkeypatch.setattr("app.main.forward_event", normalized_forward)
    monkeypatch.setattr("app.main.forward_raw_callback", raw_forward)
    settings = Settings(
        ttlock_webhook_secret=SecretStr(SECRET),
        ha_webhook_url="http://home-assistant.local/api/webhook/normalized-secret",
        ha_ttlock_webhook_url="http://home-assistant.local/api/webhook/integration-secret",
    )

    with TestClient(create_app(settings)) as relay_client:
        response = relay_client.post(
            URL,
            data={"records": json.dumps([event(recordId=9006)])},
        )

    assert response.status_code == 503
    normalized_forward.assert_not_awaited()


def test_oversized_body_is_rejected(client: TestClient, forward: AsyncMock) -> None:
    response = client.post(URL, json={"padding": "x" * 1200})
    assert response.status_code == 413
    forward.assert_not_awaited()


def test_unknown_event_is_accepted(client: TestClient, forward: AsyncMock) -> None:
    assert client.post(URL, json=event(recordId=9002, recordType=999)).status_code == 200
    assert forward.await_args.args[0].event_type == "unknown"


def test_duplicate_event_is_not_forwarded_twice(client: TestClient, forward: AsyncMock) -> None:
    assert client.post(URL, json=event()).status_code == 200
    assert client.post(URL, json=event()).status_code == 200
    forward.assert_awaited_once()


def test_home_assistant_failure_returns_retryable_status(client: TestClient, forward: AsyncMock) -> None:
    forward.side_effect = HomeAssistantUnavailable("timeout")
    assert client.post(URL, json=event()).status_code == 503

    forward.reset_mock(side_effect=True)
    assert client.post(URL, json=event()).status_code == 200
    forward.assert_awaited_once()


def test_rate_limit_is_enforced(forward: AsyncMock) -> None:
    settings = Settings(
        ttlock_webhook_secret=SecretStr(SECRET),
        ha_webhook_url="http://home-assistant.local:8123/api/webhook/separate-secret",
        rate_limit_per_minute=1,
    )
    with TestClient(create_app(settings)) as limited:
        assert limited.post(URL, json=event()).status_code == 200
        assert limited.post(URL, json=event(recordId=9002)).status_code == 429
