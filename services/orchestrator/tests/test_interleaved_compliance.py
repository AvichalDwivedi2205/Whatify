from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.models.contracts import InterruptRequest, StartSessionRequest
from app.models.enums import EventType, InterruptKind, UIActionType
from app.models.state import InterleavedBlockRecord, InterleavedRunRecord


async def _wait_for_interleaved(orchestrator, session_id: str, beat_id: str):  # type: ignore[no-untyped-def]
    for _ in range(80):
        run = await orchestrator.repo.get_latest_interleaved_run(session_id, beat_id=beat_id)
        if run is not None:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("timed out waiting for interleaved run")


async def _wait_for_compare_interleaved(orchestrator, session_id: str, beat_id: str):  # type: ignore[no-untyped-def]
    for _ in range(80):
        run = await orchestrator.repo.get_latest_interleaved_run(session_id, beat_id=beat_id)
        if run is not None and run.trigger == "COMPARE":
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("timed out waiting for compare-triggered interleaved run")


@pytest.mark.asyncio
async def test_interleaved_generation_is_persisted_and_emitted(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())

    run = await _wait_for_interleaved(orchestrator, session.session_id, "beat_1")

    assert run.beat_id == "beat_1"
    assert run.request_id
    assert run.model_id
    assert any(block.kind == "text" for block in run.blocks)
    assert any(block.kind == "image" for block in run.blocks)

    proof = await orchestrator.get_interleaved_proof(session.session_id, beat_id="beat_1")
    assert proof.run is not None
    assert proof.run.request_id == run.request_id
    assert any(block.kind == "text" for block in proof.run.blocks)
    assert any(block.kind == "image" for block in proof.run.blocks)

    events = await orchestrator.repo.list_events(session.session_id)
    types = [event.type for event in events]
    assert EventType.INTERLEAVED_GENERATION_STARTED.value in types
    assert EventType.INTERLEAVED_READY.value in types

    emitted = [
        event
        for event in events
        if event.type == EventType.UI_ACTION_EMITTED.value
        and event.payload.get("type") == UIActionType.SHOW_INTERLEAVED_BLOCKS.value
    ]
    assert emitted, "Expected SHOW_INTERLEAVED_BLOCKS action emission"


@pytest.mark.asyncio
async def test_compare_interrupt_triggers_interleaved_generation(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())
    await orchestrator.interrupt(
        session.session_id,
        InterruptRequest(kind=InterruptKind.COMPARE, question="Compare with real history"),
    )

    run = await _wait_for_compare_interleaved(orchestrator, session.session_id, "beat_1")

    assert run.trigger == "COMPARE"
    assert run.request_id
    assert any(block.kind == "text" for block in run.blocks)
    assert any(block.kind == "image" for block in run.blocks)


@pytest.mark.asyncio
async def test_interleaved_proof_handles_redacted_inline_images(orchestrator) -> None:  # type: ignore[no-untyped-def]
    session = await orchestrator.start_session(StartSessionRequest())

    run = InterleavedRunRecord(
        run_id="ilv_redacted_inline_test",
        session_id=session.session_id,
        beat_id="beat_1",
        trigger="BEAT_START",
        model_id="test-model",
        request_id="req-redacted-inline",
        created_ts=datetime.now(UTC).isoformat(),
        blocks=[
            InterleavedBlockRecord(part_order=0, kind="text", text="text block"),
            InterleavedBlockRecord(
                part_order=1,
                kind="image",
                mime_type="image/png",
                uri=None,
                inline_data_b64=None,
            ),
        ],
    )
    await orchestrator.repo.upsert_interleaved_run(session.session_id, run)

    proof = await orchestrator.get_interleaved_proof(session.session_id, beat_id="beat_1")
    assert proof.run is not None
    image_blocks = [block for block in proof.run.blocks if block.kind == "image"]
    assert image_blocks
    assert image_blocks[0].uri is not None
    assert image_blocks[0].uri.startswith("inline://missing/")
