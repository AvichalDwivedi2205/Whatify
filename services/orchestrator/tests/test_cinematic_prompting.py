from __future__ import annotations

import pytest

from app.core.agent_runtime import DeterministicAgentRuntime
from app.models.contracts import StartSessionRequest
from app.models.enums import InterleavedTrigger


@pytest.mark.asyncio
async def test_deterministic_shot_plan_supports_five_scene_visual_arc(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    state = await orchestrator.repo.get_session(session.session_id)
    beat_spec = await orchestrator.repo.get_beat_spec(session.session_id, state.beat_id)
    assert beat_spec is not None

    shot_plan = await orchestrator.agents.plan_shots(beat_spec)

    assert len(shot_plan.shots) == 5
    assert len(shot_plan.hero_shots) == 1
    assert all("no typography" in shot.prompt.lower() for shot in shot_plan.shots)


@pytest.mark.asyncio
async def test_deterministic_interleaved_story_returns_multiple_scene_pairs() -> None:
    runtime = DeterministicAgentRuntime()
    beat_spec = await runtime.plan_beat(
        session_id="session_test",
        beat_id="beat_1",
        beat_index=1,
        context={"divergence_point": "What if Baghdad perfected printing in 800 CE?"},
    )

    generation = await runtime.generate_interleaved_story(
        session_id="session_test",
        beat_id="beat_1",
        beat_index=1,
        beat_spec=beat_spec,
        trigger=InterleavedTrigger.BEAT_START,
        question=None,
    )

    text_blocks = [block for block in generation.blocks if block.kind == "text"]
    image_blocks = [block for block in generation.blocks if block.kind == "image"]

    assert len(text_blocks) == 4
    assert len(image_blocks) == 4
    assert generation.blocks[0].kind == "text"
    assert generation.blocks[1].kind == "image"
