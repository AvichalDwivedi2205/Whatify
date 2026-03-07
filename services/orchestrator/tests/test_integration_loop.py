from __future__ import annotations

import pytest

from app.models.contracts import ChoiceRequest, InterruptRequest, StartSessionRequest
from app.models.enums import InterruptKind, Mode


@pytest.mark.asyncio
async def test_six_beat_happy_path_with_interrupt(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    state = await orchestrator.repo.get_session(session.session_id)
    total_beats = state.target_beats

    for index in range(total_beats):
        state = await orchestrator.repo.get_session(session.session_id)
        beat_spec = await orchestrator.repo.get_beat_spec(session.session_id, state.beat_id)
        assert beat_spec is not None

        await orchestrator.interrupt(
            session.session_id,
            InterruptRequest(kind=InterruptKind.WHY, question="why this happened"),
        )

        result = await orchestrator.choose(
            session.session_id,
            ChoiceRequest(choice_id=beat_spec.choices[0].choice_id),
        )
        if index < total_beats - 1:
            resumed = await orchestrator.continue_session(session.session_id)
            assert resumed.ok is True

    assert result.ok is True
    final_state = await orchestrator.repo.get_session(session.session_id)
    assert final_state.mode == Mode.COMPLETE
