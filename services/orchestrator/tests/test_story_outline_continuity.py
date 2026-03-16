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


class CapturingRuntime(DeterministicAgentRuntime):
    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    async def plan_beat(self, *, session_id: str, beat_id: str, beat_index: int, context: dict[str, object]):  # type: ignore[override]
        self.contexts.append(dict(context))
        beat = await super().plan_beat(
            session_id=session_id,
            beat_id=beat_id,
            beat_index=beat_index,
            context=context,
        )
        beat.act_title = f"Act {beat_index}"
        beat.act_time_label = f"Era {beat_index}"
        beat.transition_hook = f"Hook {beat_index}"
        return beat


@pytest.mark.asyncio
async def test_story_outline_passes_previous_act_context_to_later_beats() -> None:
    runtime = CapturingRuntime()
    orchestrator = OrchestratorService(
        repo=InMemoryRepository(),
        action_bus=ActionBus(),
        caption_bus=CaptionBus(),
        agents=runtime,
        dispatcher=NoopDispatcher(),
        orchestrator_callback_url="http://localhost:8080/api/v1/assets/jobs/callback",
    )

    session = await orchestrator.start_session(
        StartSessionRequest(
            auto_run=False,
            divergence_point="What if Apollo 11 failed to return from the Moon?",
        )
    )
    state = await orchestrator.repo.get_session(session.session_id)
    assert state is not None
    state.target_beats = 3
    await orchestrator.repo.upsert_session(state)

    await orchestrator._prepare_story_outline(
        state=state,
        divergence_point="What if Apollo 11 failed to return from the Moon?",
    )

    assert len(runtime.contexts) == 3
    assert runtime.contexts[0]["current_beat_objective"] == (
        "Establish the first irreversible consequence of What if Apollo 11 failed to return from the Moon?"
    )
    assert runtime.contexts[1]["previous_act_title"] == "Act 1"
    assert runtime.contexts[1]["previous_act_time_label"] == "Era 1"
    assert runtime.contexts[1]["previous_transition_hook"] == "Hook 1"
    assert runtime.contexts[1]["rolling_story_summary"]
    assert runtime.contexts[2]["previous_act_title"] == "Act 2"
    assert runtime.contexts[2]["previous_transition_hook"] == "Hook 2"
