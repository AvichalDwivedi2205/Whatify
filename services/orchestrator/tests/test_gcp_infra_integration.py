from __future__ import annotations

import asyncio
import json
import os
import time
from uuid import uuid4

import pytest
from google.api_core.exceptions import AlreadyExists, NotFound  # type: ignore[import-untyped]
from google.cloud import pubsub_v1  # type: ignore[import-untyped]

from app.models.contracts import AssetJob, BeatSpec, Choice, EventRecord
from app.models.enums import AssetStatus, AssetType, EventType, Mode
from app.models.state import (
    AssetRecord,
    BeatSummary,
    InterleavedBlockRecord,
    InterleavedRunRecord,
    SessionHotState,
    TimelineEdge,
)
from app.queue.dispatcher import PubSubAssetDispatcher
from app.storage.gcp_repo import create_gcp_repository_from_env

pytestmark = pytest.mark.integration


def _require_live_infra() -> None:
    if os.getenv("WHATIF_RUN_GCP_INFRA_TESTS", "false").lower() != "true":
        pytest.skip("Set WHATIF_RUN_GCP_INFRA_TESTS=true to run live GCP infra tests")

    required = (
        "GOOGLE_CLOUD_PROJECT",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"Missing required environment variables: {', '.join(missing)}")


@pytest.mark.asyncio
async def test_gcp_repository_roundtrip() -> None:
    _require_live_infra()
    repo = create_gcp_repository_from_env()

    session_id = f"s_{uuid4().hex[:12]}"
    state = SessionHotState(
        session_id=session_id,
        branch_id=f"b_{uuid4().hex[:8]}",
        beat_id="beat_1",
        beat_index=1,
        mode=Mode.STORY,
        pacing="normal",
        video_budget_remaining=3,
    )

    try:
        await repo.upsert_session(state)
        loaded = await repo.get_session(session_id)
        assert loaded.session_id == session_id
        assert loaded.mode == Mode.STORY

        await repo.set_session_context(session_id, {"divergence_point": "What if Alexandria survived?"})
        context = await repo.get_session_context(session_id)
        assert context["divergence_point"] == "What if Alexandria survived?"

        event = EventRecord(
            event_id=f"evt_{uuid4().hex[:10]}",
            session_id=session_id,
            branch_id=state.branch_id,
            beat_id=state.beat_id,
            type=EventType.BEAT_STARTED.value,
            payload={"objective": "test"},
        )
        await repo.append_event(event)
        events = await repo.list_events(session_id)
        assert any(item.event_id == event.event_id for item in events)

        beat_spec = BeatSpec(
            beat_id="beat_1",
            objective="Test objective",
            setup="Test setup",
            escalation="Test escalation",
            choices=[
                Choice(choice_id="c1", label="Option one", consequence_hint="A"),
                Choice(choice_id="c2", label="Option two", consequence_hint="B"),
            ],
            consequence_seed="Test seed",
            transition_hook="Test hook",
            active_entities=["entity_1"],
        )
        await repo.set_beat_spec(session_id, "beat_1", beat_spec)
        loaded_spec = await repo.get_beat_spec(session_id, "beat_1")
        assert loaded_spec is not None
        assert loaded_spec.objective == beat_spec.objective

        summary = BeatSummary(
            beat_id="beat_1",
            summary_5_lines=["line1", "line2", "line3", "line4", "line5"],
            new_facts=["fact_1"],
            new_entities=["entity_1"],
            causal_delta="c1 shifted power",
            open_threads=["thread_1"],
            asset_refs=["asset_1"],
        )
        await repo.add_beat_summary(session_id, summary)
        summaries = await repo.recent_beat_summaries(session_id, limit=2)
        assert summaries[-1].beat_id == "beat_1"

        edge = TimelineEdge(
            edge_id=f"edge_{uuid4().hex[:10]}",
            from_node="beat_1",
            to_node="beat_1:consequence:c1",
            edge_type="CHOICE_CAUSES",
            justification="choice c1 was selected",
            supporting_event_ids=[event.event_id],
            confidence=0.9,
        )
        await repo.add_timeline_edge(session_id, edge)
        edges = await repo.list_timeline_edges(session_id)
        assert any(item.edge_id == edge.edge_id for item in edges)

        asset = AssetRecord(
            asset_id=f"asset_{uuid4().hex[:10]}",
            type=AssetType.STORYBOARD,
            session_id=session_id,
            branch_id=state.branch_id,
            beat_id=state.beat_id,
            shot_id="shot_1",
            prompt_hash="hash_1",
            status=AssetStatus.PENDING,
            reuse_tags=["entity_1"],
        )
        await repo.upsert_asset(session_id, asset)
        loaded_asset = await repo.get_asset(session_id, asset.asset_id)
        assert loaded_asset is not None
        assert loaded_asset.status == AssetStatus.PENDING

        interleaved = InterleavedRunRecord(
            run_id=f"ilv_{uuid4().hex[:10]}",
            session_id=session_id,
            beat_id="beat_1",
            trigger="BEAT_START",
            model_id="gemini-2.5-flash-image",
            request_id=f"req_{uuid4().hex[:8]}",
            created_ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            blocks=[
                InterleavedBlockRecord(part_order=0, kind="text", text="Scene text"),
                InterleavedBlockRecord(
                    part_order=1,
                    kind="image",
                    mime_type="image/png",
                    uri="gs://bucket/path/frame.png",
                ),
            ],
        )
        await repo.upsert_interleaved_run(session_id, interleaved)
        loaded_interleaved = await repo.get_latest_interleaved_run(session_id, beat_id="beat_1")
        assert loaded_interleaved is not None
        assert loaded_interleaved.run_id == interleaved.run_id
        assert len(loaded_interleaved.blocks) == 2

        first_seq = await repo.next_action_seq(session_id)
        second_seq = await repo.next_action_seq(session_id)
        assert second_seq == first_seq + 1
    finally:
        await repo.close()
        repo.firestore.close()


@pytest.mark.asyncio
async def test_pubsub_dispatch_roundtrip() -> None:
    _require_live_infra()

    project_id = os.environ["GOOGLE_CLOUD_PROJECT"]
    topic_id = f"whatif-integ-{uuid4().hex[:8]}"
    subscription_id = f"{topic_id}-sub"

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    topic_path = publisher.topic_path(project_id, topic_id)
    subscription_path = subscriber.subscription_path(project_id, subscription_id)
    dispatcher = PubSubAssetDispatcher(project_id=project_id, topic_id=topic_id)

    try:
        await asyncio.to_thread(_setup_topic_and_subscription, publisher, subscriber, topic_path, subscription_path)

        job = AssetJob(
            job_id=f"job_{uuid4().hex[:10]}",
            type=AssetType.STORYBOARD,
            session_id=f"s_{uuid4().hex[:8]}",
            branch_id=f"b_{uuid4().hex[:8]}",
            beat_id="beat_1",
            shot_id="shot_1",
            prompt="cinematic harbor command room",
        )
        await dispatcher.dispatch(
            asset_id=f"asset_{uuid4().hex[:10]}",
            job=job,
            callback_url="http://localhost:8080/api/v1/assets/jobs/callback",
        )

        raw_message = await asyncio.to_thread(_pull_one_message, subscriber, subscription_path)
        payload = json.loads(raw_message)

        assert payload["session_id"] == job.session_id
        assert payload["beat_id"] == job.beat_id
        assert payload["asset_type"] == job.type.value
        assert payload["orchestrator_callback_url"].startswith("http://localhost")
    finally:
        await asyncio.to_thread(_cleanup_pubsub, publisher, subscriber, topic_path, subscription_path)
        publisher.transport.close()
        subscriber.close()


def _setup_topic_and_subscription(
    publisher: pubsub_v1.PublisherClient,
    subscriber: pubsub_v1.SubscriberClient,
    topic_path: str,
    subscription_path: str,
) -> None:
    try:
        publisher.create_topic(request={"name": topic_path})
    except AlreadyExists:
        pass

    try:
        subscriber.create_subscription(request={"name": subscription_path, "topic": topic_path})
    except AlreadyExists:
        pass


def _pull_one_message(subscriber: pubsub_v1.SubscriberClient, subscription_path: str) -> str:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = subscriber.pull(
            request={"subscription": subscription_path, "max_messages": 1},
            timeout=5,
        )
        if response.received_messages:
            received = response.received_messages[0]
            subscriber.acknowledge(
                request={"subscription": subscription_path, "ack_ids": [received.ack_id]},
            )
            return received.message.data.decode("utf-8")
        time.sleep(0.5)
    raise AssertionError("did not receive Pub/Sub message within timeout")


def _cleanup_pubsub(
    publisher: pubsub_v1.PublisherClient,
    subscriber: pubsub_v1.SubscriberClient,
    topic_path: str,
    subscription_path: str,
) -> None:
    try:
        subscriber.delete_subscription(request={"subscription": subscription_path})
    except NotFound:
        pass

    try:
        publisher.delete_topic(request={"topic": topic_path})
    except NotFound:
        pass
