from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.contracts import InterleavedBlock, InterleavedGeneration, StartSessionRequest
from app.models.enums import AssetStatus, AssetType, InterleavedTrigger
from app.models.state import AssetRecord, InterleavedBlockRecord, InterleavedRunRecord


@pytest.mark.asyncio
async def test_visual_state_returns_ready_assets_and_cached_interleaved(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest(auto_run=False, divergence_point=None))

    run = InterleavedRunRecord(
        run_id="ilv_visual_state_test",
        session_id=session.session_id,
        beat_id="beat_1",
        trigger="BEAT_START",
        model_id="test-model",
        request_id="req-visual-state",
        created_ts=datetime.now(UTC).isoformat(),
        blocks=[
            InterleavedBlockRecord(part_order=0, kind="text", text="A tense opening."),
            InterleavedBlockRecord(part_order=1, kind="image", mime_type="image/png", uri=None, inline_data_b64=None),
        ],
    )
    await orchestrator.repo.upsert_interleaved_run(session.session_id, run)
    orchestrator._interleaved_generations[(session.session_id, "beat_1")] = InterleavedGeneration(
        run_id=run.run_id,
        session_id=session.session_id,
        beat_id="beat_1",
        trigger=InterleavedTrigger.BEAT_START,
        model_id="test-model",
        request_id="req-visual-state",
        blocks=[
            InterleavedBlock(part_order=0, kind="text", text="A tense opening."),
            InterleavedBlock(
                part_order=1,
                kind="image",
                mime_type="image/png",
                inline_data_b64="aGVsbG8=",
            ),
        ],
    )
    await orchestrator.repo.upsert_asset(
        session.session_id,
        AssetRecord(
            asset_id="asset_storyboard_1",
            type=AssetType.STORYBOARD,
            session_id=session.session_id,
            branch_id=session.branch_id,
            beat_id="beat_1",
            shot_id="shot_01",
            prompt_hash="prompt-storyboard",
            status=AssetStatus.READY,
            uri="gs://whatif-tests/beat_1/frame.png",
        ),
    )
    await orchestrator.repo.upsert_asset(
        session.session_id,
        AssetRecord(
            asset_id="asset_video_1",
            type=AssetType.HERO_VIDEO,
            session_id=session.session_id,
            branch_id=session.branch_id,
            beat_id="beat_1",
            shot_id="hero_01",
            prompt_hash="prompt-video",
            status=AssetStatus.READY,
            uri="gs://whatif-tests/beat_1/hero.mp4",
        ),
    )

    visual_state = await orchestrator.get_visual_state(session.session_id, "beat_1")

    assert visual_state.session_id == session.session_id
    assert visual_state.beat_id == "beat_1"
    assert visual_state.hero_video_uri == "gs://whatif-tests/beat_1/hero.mp4"
    assert len(visual_state.storyboard_frames) == 1
    assert visual_state.storyboard_frames[0].shot_id == "shot_01"
    assert visual_state.storyboard_frames[0].uri == "gs://whatif-tests/beat_1/frame.png"
    assert visual_state.interleaved_run is not None
    image_blocks = [block for block in visual_state.interleaved_run.blocks if block.kind == "image"]
    assert image_blocks
    assert image_blocks[0].inline_data_b64 == "aGVsbG8="
