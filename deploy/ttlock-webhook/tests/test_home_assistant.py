from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.home_assistant import HomeAssistantUnavailable, forward_event
from app.models import NormalizedEvent


@pytest.fixture
def normalized() -> NormalizedEvent:
    return NormalizedEvent.model_validate(
        {
            "event_type": "unlock",
            "lock_id": "1",
            "timestamp": "2026-09-11T20:15:30Z",
            "event_id": "event-1",
            "raw_event_type": 46,
            "success": True,
        }
    )


class FakeClient:
    def __init__(self, responses: list[object], **_: object) -> None:
        self.responses = responses
        self.post = AsyncMock(side_effect=responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timeout"), httpx.ConnectError("down")])
async def test_transient_network_failure_retries_three_times(
    normalized: NormalizedEvent, failure: Exception
) -> None:
    fake = FakeClient([failure, failure, failure])
    with patch("app.home_assistant.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(HomeAssistantUnavailable):
            await forward_event(normalized, "http://ha/webhook", 0.1, lambda **kwargs: fake)
    assert fake.post.await_count == 3


async def test_home_assistant_500_retries_then_succeeds(normalized: NormalizedEvent) -> None:
    fake = FakeClient(
        [httpx.Response(500), httpx.Response(500), httpx.Response(204)]
    )
    with patch("app.home_assistant.asyncio.sleep", new=AsyncMock()):
        await forward_event(normalized, "http://ha/webhook", 0.1, lambda **kwargs: fake)
    assert fake.post.await_count == 3


async def test_non_retryable_home_assistant_rejection_stops(normalized: NormalizedEvent) -> None:
    fake = FakeClient([httpx.Response(400)])
    with pytest.raises(HomeAssistantUnavailable):
        await forward_event(normalized, "http://ha/webhook", 0.1, lambda **kwargs: fake)
    assert fake.post.await_count == 1
