from __future__ import annotations

import pytest

from app.models.contracts import ChoiceRequest, StartSessionRequest


@pytest.mark.asyncio
async def test_memory_compaction_stores_beat_summary(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    state = await orchestrator.repo.get_session(session.session_id)
    beat_spec = await orchestrator.repo.get_beat_spec(session.session_id, state.beat_id)
    assert beat_spec is not None

    await orchestrator.choose(session.session_id, ChoiceRequest(choice_id=beat_spec.choices[0].choice_id))

    summaries = await orchestrator.repo.recent_beat_summaries(session.session_id, limit=1)
    assert len(summaries) == 1
    assert len(summaries[0].summary_5_lines) == 5
