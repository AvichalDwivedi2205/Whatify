from __future__ import annotations

import pytest

from app.models.contracts import AckRequest, StartSessionRequest


@pytest.mark.asyncio
async def test_action_ack_idempotency(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    state = await orchestrator.get_state(session.session_id)
    assert state.pending_actions > 0

    first_state = await orchestrator.repo.get_session(session.session_id)
    action_id = first_state.pending_actions[0]

    first_ack = await orchestrator.ack_action(session.session_id, AckRequest(action_id=action_id))
    second_ack = await orchestrator.ack_action(session.session_id, AckRequest(action_id=action_id))

    assert first_ack.ok is True
    assert second_ack.ok is False
