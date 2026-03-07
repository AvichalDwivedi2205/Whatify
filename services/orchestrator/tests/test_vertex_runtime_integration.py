from __future__ import annotations

import os

import pytest

from app.core.agent_runtime import VertexAgentRuntime

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_vertex_runtime_plan_beat_roundtrip() -> None:
    if os.getenv("WHATIF_RUN_VERTEX_TESTS", "false").lower() != "true":
        pytest.skip("Set WHATIF_RUN_VERTEX_TESTS=true to run live Vertex tests")

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        pytest.skip("GOOGLE_CLOUD_PROJECT is required for live Vertex tests")

    runtime = VertexAgentRuntime(
        project=project,
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        model=os.getenv("WHATIF_VERTEX_MODEL", "gemini-2.5-pro"),
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
async def test_vertex_runtime_safety_rewrite_roundtrip() -> None:
    if os.getenv("WHATIF_RUN_VERTEX_TESTS", "false").lower() != "true":
        pytest.skip("Set WHATIF_RUN_VERTEX_TESTS=true to run live Vertex tests")

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        pytest.skip("GOOGLE_CLOUD_PROJECT is required for live Vertex tests")

    runtime = VertexAgentRuntime(
        project=project,
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        model=os.getenv("WHATIF_VERTEX_MODEL", "gemini-2.5-pro"),
    )

    rewritten = await runtime.safety_rewrite("This includes graphic violence in wording")
    assert rewritten
    assert isinstance(rewritten, str)
