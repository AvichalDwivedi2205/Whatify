from __future__ import annotations

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


@pytest.fixture
def orchestrator() -> OrchestratorService:
    repo = InMemoryRepository()
    action_bus = ActionBus()
    caption_bus = CaptionBus()
    return OrchestratorService(
        repo=repo,
        action_bus=action_bus,
        caption_bus=caption_bus,
        agents=DeterministicAgentRuntime(),
        dispatcher=NoopDispatcher(),
        orchestrator_callback_url="http://localhost:8080/api/v1/assets/jobs/callback",
    )


@pytest.fixture
async def started_session(orchestrator: OrchestratorService) -> tuple[OrchestratorService, str]:
    response = await orchestrator.start_session(StartSessionRequest())
    return orchestrator, response.session_id
