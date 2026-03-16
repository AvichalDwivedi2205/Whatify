from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.live.director import VoiceDirectorTools
from app.models.contracts import ChoiceRequest, DirectorSignalRequest, StartSessionRequest
from app.models.enums import DirectorSignalType, Mode


@pytest.mark.asyncio
async def test_director_signal_interrupt_change_tone(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())

    response = await orchestrator.handle_director_signal(
        session.session_id,
        DirectorSignalRequest(
            type=DirectorSignalType.INTERRUPT,
            payload={"kind": "CHANGE_TONE", "question": "somber"},
        ),
    )

    assert response.ok is True
    state = await orchestrator.repo.get_session(session.session_id)
    assert state.user_tone == "somber"


@pytest.mark.asyncio
async def test_director_signal_interrupt_why(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())

    response = await orchestrator.handle_director_signal(
        session.session_id,
        DirectorSignalRequest(
            type=DirectorSignalType.INTERRUPT,
            payload={"kind": "WHY", "question": "why this happened"},
        ),
    )

    assert response.ok is True


@pytest.mark.asyncio
async def test_director_signal_story_brief_captured(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest(auto_run=False, divergence_point=None))

    response = await orchestrator.handle_director_signal(
        session.session_id,
        DirectorSignalRequest(
            type=DirectorSignalType.STORY_BRIEF_CAPTURED,
            payload={"divergence_point": "What if industrialization started in 900 CE?"},
        ),
    )

    assert response.ok is True
    state = await orchestrator.repo.get_session(session.session_id)
    assert state.mode in {Mode.CHOICE, Mode.STORY}


@pytest.mark.asyncio
async def test_director_signal_continue_act(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    state = await orchestrator.repo.get_session(session.session_id)
    beat_spec = await orchestrator.repo.get_beat_spec(session.session_id, state.beat_id)
    assert beat_spec is not None

    await orchestrator.choose(session.session_id, ChoiceRequest(choice_id=beat_spec.choices[0].choice_id))
    intermission = await orchestrator.repo.get_session(session.session_id)
    assert intermission.mode.value == "INTERMISSION"

    response = await orchestrator.handle_director_signal(
        session.session_id,
        DirectorSignalRequest(type=DirectorSignalType.CONTINUE_ACT, payload={}),
    )

    assert response.ok is True
    resumed = await orchestrator.repo.get_session(session.session_id)
    assert resumed.beat_index == 2


@pytest.mark.asyncio
async def test_live_tool_continue_ignores_non_summary_story(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    tools = VoiceDirectorTools(orchestrator)
    tool_context = SimpleNamespace(session=SimpleNamespace(id=session.session_id))

    response = await tools.signal_continue_act(tool_context=tool_context)

    assert response == {"ok": False, "error": "session not ready to continue"}
    state = await orchestrator.repo.get_session(session.session_id)
    assert state.beat_index == 1


@pytest.mark.asyncio
async def test_live_tool_continue_allows_story_summary(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    await orchestrator.repo.set_session_context(
        session.session_id,
        {"scene_snapshot": {"phase": "actSummary", "story_mode": {"summary": True}}},
    )
    tools = VoiceDirectorTools(orchestrator)
    tool_context = SimpleNamespace(session=SimpleNamespace(id=session.session_id))

    response = await tools.signal_continue_act(tool_context=tool_context)

    assert response == {"ok": True}
    state = await orchestrator.repo.get_session(session.session_id)
    assert state.beat_index == 2
