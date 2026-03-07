from __future__ import annotations

import pytest

from app.models.contracts import AssetExplainRequest, BeginSessionRequest, ChoiceRequest, StartSessionRequest
from app.models.enums import Mode


@pytest.mark.asyncio
async def test_start_session_onboarding_mode_without_auto_run(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest(auto_run=False, divergence_point=None))
    state = await orchestrator.repo.get_session(session.session_id)
    assert state.mode == Mode.ONBOARDING
    beat_spec = await orchestrator.repo.get_beat_spec(session.session_id, "beat_1")
    assert beat_spec is None
    context = await orchestrator.repo.get_session_context(session.session_id)
    assert context["divergence_point"] == ""


@pytest.mark.asyncio
async def test_begin_session_runs_first_beat_and_sets_target(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest(auto_run=False, divergence_point=None))
    begin = await orchestrator.begin_session(
        session.session_id,
        BeginSessionRequest(divergence_point="What if ocean trade and science advanced in sync?", tone="cinematic"),
    )
    assert begin.ok is True

    state = await orchestrator.repo.get_session(session.session_id)
    assert 4 <= state.target_beats <= 8
    assert state.mode == Mode.CHOICE
    beat_spec = await orchestrator.repo.get_beat_spec(session.session_id, state.beat_id)
    assert beat_spec is not None


@pytest.mark.asyncio
async def test_choose_requires_continue_for_next_beat(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    state = await orchestrator.repo.get_session(session.session_id)
    beat_spec = await orchestrator.repo.get_beat_spec(session.session_id, state.beat_id)
    assert beat_spec is not None

    choose = await orchestrator.choose(
        session.session_id,
        ChoiceRequest(choice_id=beat_spec.choices[0].choice_id),
    )
    assert choose.ok is True

    intermission_state = await orchestrator.repo.get_session(session.session_id)
    assert intermission_state.mode == Mode.INTERMISSION
    assert intermission_state.awaiting_continue is True

    continued = await orchestrator.continue_session(session.session_id)
    assert continued.ok is True

    next_state = await orchestrator.repo.get_session(session.session_id)
    assert next_state.beat_index == 2


@pytest.mark.asyncio
async def test_asset_explain_uses_snapshot_context(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    await orchestrator.update_scene_snapshot(session.session_id, {"scene_title": "Act frame", "media": ["shot_1"]})
    result = await orchestrator.asset_explain(
        session.session_id,
        AssetExplainRequest(question="What am I seeing in this frame?"),
    )
    assert result.ok is True
    assert result.message
