from __future__ import annotations

import os

import pytest

from app.core.agent_runtime import GeminiAgentRuntime

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_gemini_runtime_plan_beat_roundtrip() -> None:
    if os.getenv("WHATIF_RUN_GEMINI_TESTS", os.getenv("WHATIF_RUN_VERTEX_TESTS", "false")).lower() != "true":
        pytest.skip("Set WHATIF_RUN_GEMINI_TESTS=true to run live Gemini tests")

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY or GOOGLE_API_KEY is required for live Gemini tests")

    runtime = GeminiAgentRuntime(
        api_key=api_key,
        model=os.getenv("WHATIF_GEMINI_MODEL", os.getenv("WHATIF_VERTEX_MODEL", "gemini-2.5-flash")),
    )

    beat = await runtime.plan_beat(
        session_id="s_test",
        beat_id="beat_1",
        beat_index=1,
        context={"divergence_point": "What if Alexandria never burned?"},
    )
    assert beat.beat_id == "beat_1"
    assert len(beat.choices) >= 2


@pytest.mark.asyncio
async def test_gemini_runtime_safety_rewrite_roundtrip() -> None:
    if os.getenv("WHATIF_RUN_GEMINI_TESTS", os.getenv("WHATIF_RUN_VERTEX_TESTS", "false")).lower() != "true":
        pytest.skip("Set WHATIF_RUN_GEMINI_TESTS=true to run live Gemini tests")

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY or GOOGLE_API_KEY is required for live Gemini tests")

    runtime = GeminiAgentRuntime(
        api_key=api_key,
        model=os.getenv("WHATIF_GEMINI_MODEL", os.getenv("WHATIF_VERTEX_MODEL", "gemini-2.5-flash")),
    )

    rewritten = await runtime.safety_rewrite("This includes graphic violence in wording")
    assert rewritten
    assert isinstance(rewritten, str)
