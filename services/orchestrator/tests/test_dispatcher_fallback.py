from __future__ import annotations

import pytest

from app.models.contracts import AssetJob
from app.models.enums import AssetType
import app.queue.dispatcher as dispatcher_module
from app.queue.dispatcher import FallbackAssetDispatcher
from app.queue.dispatcher import create_dispatcher_from_env


class FailingDispatcher:
    async def dispatch(self, *, asset_id: str, job: AssetJob, callback_url: str) -> None:
        _ = asset_id
        _ = job
        _ = callback_url
        raise RuntimeError("worker unavailable")


class DummyResponse:
    def raise_for_status(self) -> None:
        return None


@pytest.mark.asyncio
async def test_fallback_dispatcher_emits_stub_storyboard_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args
            _ = kwargs

        async def __aenter__(self) -> DummyClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = exc_type
            _ = exc
            _ = tb

        async def post(self, url: str, json: dict[str, object]) -> DummyResponse:
            requests.append((url, json))
            return DummyResponse()

    monkeypatch.setattr(dispatcher_module.httpx, "AsyncClient", DummyClient)

    dispatcher = FallbackAssetDispatcher(primary=FailingDispatcher())
    job = AssetJob(
        job_id="job_1",
        type=AssetType.STORYBOARD,
        session_id="s1",
        branch_id="b1",
        beat_id="beat_1",
        shot_id="shot_01",
        prompt="A silent command module drifting above the moon.",
    )

    await dispatcher.dispatch(asset_id="asset_1", job=job, callback_url="http://localhost:8080/callback")

    assert len(requests) == 1
    url, payload = requests[0]
    assert url == "http://localhost:8080/callback"
    assert payload["status"] == "ready"
    assert isinstance(payload["uri"], str)
    assert str(payload["uri"]).startswith("data:image/svg+xml")


def test_create_dispatcher_from_env_defaults_to_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATIF_ENV", "local")
    monkeypatch.delenv("WHATIF_WORKER_URL", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)

    dispatcher = create_dispatcher_from_env()

    assert isinstance(dispatcher, FallbackAssetDispatcher)
