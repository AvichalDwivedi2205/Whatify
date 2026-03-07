from __future__ import annotations

import pytest

from app.core.agent_runtime import DeterministicAgentRuntime


@pytest.mark.asyncio
async def test_safety_rewrite_preserves_intent() -> None:
    runtime = DeterministicAgentRuntime()
    text = "This beat has graphic violence but should keep strategic tension."
    rewritten = await runtime.safety_rewrite(text)
    assert "redacted" in rewritten
    assert "strategic tension" in rewritten
