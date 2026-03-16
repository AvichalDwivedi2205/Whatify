from __future__ import annotations

import asyncio

import pytest

from app.core.agent_runtime import DeterministicAgentRuntime
from app.core.orchestrator import OrchestratorService
from app.models.contracts import StartSessionRequest
from app.queue.dispatcher import AssetDispatcherProtocol
from app.storage.memory_repo import InMemoryRepository
from app.streams.action_bus import ActionBus
from app.streams.caption_bus import CaptionBus


class NoopDispatcher(AssetDispatcherProtocol):
    async def dispatch(self, *, asset_id: str, job, callback_url: str) -> None:  # type: ignore[no-untyped-def]
        _ = asset_id
        _ = job
        _ = callback_url


class SlowInterleavedRuntime(DeterministicAgentRuntime):
    async def generate_interleaved_story(self, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(1.0)
        return await super().generate_interleaved_story(**kwargs)


@pytest.mark.asyncio
async def test_start_session_does_not_block_on_interleaved_generation() -> None:
    orchestrator = OrchestratorService(
        repo=InMemoryRepository(),
        action_bus=ActionBus(),
        caption_bus=CaptionBus(),
        agents=SlowInterleavedRuntime(),
        dispatcher=NoopDispatcher(),
        orchestrator_callback_url="http://localhost:8080/api/v1/assets/jobs/callback",
    )

    response = await asyncio.wait_for(orchestrator.start_session(StartSessionRequest()), timeout=0.25)

    state = await orchestrator.repo.get_session(response.session_id)
    assert state.mode.value == "STORY"
    assert state.current_phase == "story"
