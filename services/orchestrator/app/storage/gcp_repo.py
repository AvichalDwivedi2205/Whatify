from __future__ import annotations

import inspect
import json
import os
from dataclasses import asdict
from typing import Any, cast

from google.cloud import firestore
from google.cloud.firestore import AsyncClient
from upstash_redis.asyncio import Redis as UpstashRedis

from app.models.contracts import BeatSpec, EventRecord
from app.models.enums import AssetStatus, AssetType, Mode
from app.models.state import (
    AssetRecord,
    BeatSummary,
    InterleavedBlockRecord,
    InterleavedRunRecord,
    SessionHotState,
    TimelineEdge,
)
from app.utils.time import utc_now_iso


class GcpRepository:
    def __init__(
        self,
        *,
        upstash_redis_rest_url: str,
        upstash_redis_rest_token: str,
        project_id: str,
        firestore_database: str = "(default)",
    ) -> None:
        self.redis: Any = UpstashRedis(
            url=upstash_redis_rest_url,
            token=upstash_redis_rest_token,
        )
        self.firestore = AsyncClient(project=project_id, database=firestore_database)

    async def upsert_session(self, state: SessionHotState) -> None:
        key = self._session_state_key(state.session_id)
        payload = {
            "session_id": state.session_id,
            "branch_id": state.branch_id,
            "beat_id": state.beat_id,
            "beat_index": state.beat_index,
            "mode": state.mode.value,
            "pacing": state.pacing,
            "video_budget_remaining": state.video_budget_remaining,
            "target_beats": state.target_beats,
            "current_phase": state.current_phase,
            "awaiting_continue": state.awaiting_continue,
            "prefetch_queue_status": state.prefetch_queue_status,
            "active_entities": state.active_entities,
            "pending_actions": state.pending_actions,
            "user_tone": state.user_tone,
        }
        await self._hot_set(key, json.dumps(payload))

    async def get_session(self, session_id: str) -> SessionHotState:
        key = self._session_state_key(session_id)
        raw = await self._hot_get(key)
        if raw is None:
            raise KeyError(session_id)
        payload = json.loads(raw)
        return SessionHotState(
            session_id=payload["session_id"],
            branch_id=payload["branch_id"],
            beat_id=payload["beat_id"],
            beat_index=int(payload["beat_index"]),
            mode=Mode(payload["mode"]),
            pacing=payload["pacing"],
            video_budget_remaining=int(payload["video_budget_remaining"]),
            target_beats=int(payload.get("target_beats", 6)),
            current_phase=str(payload.get("current_phase", "onboarding")),
            awaiting_continue=bool(payload.get("awaiting_continue", False)),
            prefetch_queue_status=payload.get("prefetch_queue_status", "idle"),
            active_entities=list(payload.get("active_entities", [])),
            pending_actions=list(payload.get("pending_actions", [])),
            user_tone=payload.get("user_tone", "cinematic"),
        )

    async def set_session_context(self, session_id: str, data: dict[str, Any]) -> None:
        key = self._session_context_key(session_id)
        existing = await self.get_session_context(session_id)
        existing.update(data)
        await self._hot_set(key, json.dumps(existing))

    async def get_session_context(self, session_id: str) -> dict[str, Any]:
        key = self._session_context_key(session_id)
        raw = await self._hot_get(key)
        if raw is None:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("session context payload must be an object")
        return cast(dict[str, Any], payload)

    async def append_event(self, record: EventRecord) -> None:
        await (
            self._session_doc(record.session_id)
            .collection("events")
            .document(record.event_id)
            .set(record.model_dump(mode="json"))
        )

    async def list_events(self, session_id: str, limit: int | None = None) -> list[EventRecord]:
        events_ref = self._session_doc(session_id).collection("events")
        if limit is None:
            query = events_ref.order_by("ts")
            docs = [doc async for doc in query.stream()]
            return [EventRecord.model_validate(doc.to_dict()) for doc in docs]

        query_desc = events_ref.order_by("ts", direction=firestore.Query.DESCENDING).limit(limit)
        docs_desc = [doc async for doc in query_desc.stream()]
        docs_desc.reverse()
        return [EventRecord.model_validate(doc.to_dict()) for doc in docs_desc]

    async def set_beat_spec(self, session_id: str, beat_id: str, spec: BeatSpec) -> None:
        await (
            self._session_doc(session_id)
            .collection("beat_specs")
            .document(beat_id)
            .set(spec.model_dump(mode="json"))
        )

    async def get_beat_spec(self, session_id: str, beat_id: str) -> BeatSpec | None:
        snap = await self._session_doc(session_id).collection("beat_specs").document(beat_id).get()
        if not snap.exists:
            return None
        return BeatSpec.model_validate(snap.to_dict())

    async def recent_beat_summaries(self, session_id: str, limit: int = 2) -> list[BeatSummary]:
        query = (
            self._session_doc(session_id)
            .collection("beat_summaries")
            .order_by("created_ts", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        docs = [doc async for doc in query.stream()]
        docs.reverse()
        return [self._decode_beat_summary(doc.to_dict()) for doc in docs]

    async def add_beat_summary(self, session_id: str, summary: BeatSummary) -> None:
        payload = asdict(summary)
        payload["created_ts"] = utc_now_iso()
        await (
            self._session_doc(session_id)
            .collection("beat_summaries")
            .document(summary.beat_id)
            .set(payload)
        )

    async def add_timeline_edge(self, session_id: str, edge: TimelineEdge) -> None:
        payload = asdict(edge)
        payload["created_ts"] = utc_now_iso()
        await (
            self._session_doc(session_id)
            .collection("timeline_edges")
            .document(edge.edge_id)
            .set(payload)
        )

    async def list_timeline_edges(self, session_id: str) -> list[TimelineEdge]:
        query = self._session_doc(session_id).collection("timeline_edges").order_by("created_ts")
        docs = [doc async for doc in query.stream()]
        return [self._decode_timeline_edge(doc.to_dict()) for doc in docs]

    async def upsert_asset(self, session_id: str, record: AssetRecord) -> None:
        payload = {
            "asset_id": record.asset_id,
            "type": record.type.value,
            "session_id": record.session_id,
            "branch_id": record.branch_id,
            "beat_id": record.beat_id,
            "shot_id": record.shot_id,
            "prompt_hash": record.prompt_hash,
            "status": record.status.value,
            "uri": record.uri,
            "reuse_tags": record.reuse_tags,
            "cost_estimate": record.cost_estimate,
            "generation_time_ms": record.generation_time_ms,
            "updated_ts": utc_now_iso(),
        }
        await self._session_doc(session_id).collection("assets").document(record.asset_id).set(payload)

    async def get_asset(self, session_id: str, asset_id: str) -> AssetRecord | None:
        snap = await self._session_doc(session_id).collection("assets").document(asset_id).get()
        if not snap.exists:
            return None
        payload = snap.to_dict()
        if payload is None:
            return None
        return self._decode_asset(payload)

    async def list_assets_for_beat(
        self,
        session_id: str,
        beat_id: str,
        *,
        asset_type: str | None = None,
        status: str | None = None,
    ) -> list[AssetRecord]:
        docs = [doc async for doc in self._session_doc(session_id).collection("assets").stream()]
        assets: list[AssetRecord] = []
        for doc in docs:
            payload = doc.to_dict()
            if payload is None:
                continue
            if str(payload.get("beat_id", "")) != beat_id:
                continue
            if asset_type is not None and str(payload.get("type", "")) != asset_type:
                continue
            if status is not None and str(payload.get("status", "")) != status:
                continue
            assets.append(self._decode_asset(payload))
        assets.sort(key=lambda asset: (asset.shot_id, asset.asset_id))
        return assets

    async def upsert_interleaved_run(self, session_id: str, record: InterleavedRunRecord) -> None:
        payload = {
            "run_id": record.run_id,
            "session_id": record.session_id,
            "beat_id": record.beat_id,
            "trigger": record.trigger,
            "model_id": record.model_id,
            "request_id": record.request_id,
            "created_ts": record.created_ts,
            "blocks": [asdict(block) for block in record.blocks],
        }
        await self._session_doc(session_id).collection("interleaved_runs").document(record.run_id).set(payload)

    async def get_latest_interleaved_run(
        self,
        session_id: str,
        beat_id: str | None = None,
    ) -> InterleavedRunRecord | None:
        runs_ref = self._session_doc(session_id).collection("interleaved_runs")

        # Avoid requiring a composite index on (beat_id, created_ts) by
        # reading a recent ordered window and filtering client-side.
        window_limit = 50 if beat_id is not None else 1
        query = runs_ref.order_by("created_ts", direction=firestore.Query.DESCENDING).limit(window_limit)
        docs = [doc async for doc in query.stream()]
        if not docs:
            return None

        for doc in docs:
            payload = doc.to_dict()
            if payload is None:
                continue
            if beat_id is not None and str(payload.get("beat_id", "")) != beat_id:
                continue
            return self._decode_interleaved_run(payload)
        return None

    async def next_action_seq(self, session_id: str) -> int:
        key = self._action_seq_key(session_id)
        value = await self._hot_incr(key)
        return int(value)

    async def close(self) -> None:
        close_method = getattr(self.redis, "aclose", None)
        if close_method is None:
            close_method = getattr(self.redis, "close", None)
        if close_method is None:
            return

        result = close_method()
        if inspect.isawaitable(result):
            await result

    async def _hot_set(self, key: str, value: str) -> None:
        await self.redis.set(key, value)

    async def _hot_get(self, key: str) -> str | None:
        raw = await self.redis.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, (dict, list)):
            return json.dumps(raw)
        return str(raw)

    async def _hot_incr(self, key: str) -> int:
        value = await self.redis.incr(key)
        return int(value)

    def _session_doc(self, session_id: str) -> Any:
        return self.firestore.collection("sessions").document(session_id)

    @staticmethod
    def _session_state_key(session_id: str) -> str:
        return f"whatif:session:{session_id}:state"

    @staticmethod
    def _session_context_key(session_id: str) -> str:
        return f"whatif:session:{session_id}:context"

    @staticmethod
    def _action_seq_key(session_id: str) -> str:
        return f"whatif:session:{session_id}:action_seq"

    @staticmethod
    def _decode_beat_summary(payload: dict[str, Any]) -> BeatSummary:
        return BeatSummary(
            beat_id=str(payload["beat_id"]),
            summary_5_lines=list(payload.get("summary_5_lines", [])),
            new_facts=list(payload.get("new_facts", [])),
            new_entities=list(payload.get("new_entities", [])),
            causal_delta=str(payload.get("causal_delta", "")),
            open_threads=list(payload.get("open_threads", [])),
            asset_refs=list(payload.get("asset_refs", [])),
        )

    @staticmethod
    def _decode_timeline_edge(payload: dict[str, Any]) -> TimelineEdge:
        return TimelineEdge(
            edge_id=str(payload["edge_id"]),
            from_node=str(payload["from_node"]),
            to_node=str(payload["to_node"]),
            edge_type=str(payload["edge_type"]),
            justification=str(payload.get("justification", "")),
            supporting_event_ids=list(payload.get("supporting_event_ids", [])),
            confidence=float(payload.get("confidence", 0.8)),
        )

    @staticmethod
    def _decode_asset(payload: dict[str, Any]) -> AssetRecord:
        return AssetRecord(
            asset_id=str(payload["asset_id"]),
            type=AssetType(payload["type"]),
            session_id=str(payload["session_id"]),
            branch_id=str(payload["branch_id"]),
            beat_id=str(payload["beat_id"]),
            shot_id=str(payload["shot_id"]),
            prompt_hash=str(payload.get("prompt_hash", "")),
            status=AssetStatus(payload["status"]),
            uri=payload.get("uri"),
            reuse_tags=list(payload.get("reuse_tags", [])),
            cost_estimate=payload.get("cost_estimate"),
            generation_time_ms=payload.get("generation_time_ms"),
        )

    @staticmethod
    def _decode_interleaved_run(payload: dict[str, Any]) -> InterleavedRunRecord:
        raw_blocks = payload.get("blocks", [])
        blocks = [
            InterleavedBlockRecord(
                part_order=int(item.get("part_order", 0)),
                kind=str(item.get("kind", "")),
                text=item.get("text"),
                mime_type=item.get("mime_type"),
                uri=item.get("uri"),
                inline_data_b64=item.get("inline_data_b64"),
            )
            for item in raw_blocks
            if isinstance(item, dict)
        ]
        blocks.sort(key=lambda block: block.part_order)

        return InterleavedRunRecord(
            run_id=str(payload["run_id"]),
            session_id=str(payload["session_id"]),
            beat_id=str(payload["beat_id"]),
            trigger=str(payload.get("trigger", "")),
            model_id=str(payload.get("model_id", "")),
            request_id=str(payload.get("request_id", "")),
            created_ts=str(payload.get("created_ts", "")),
            blocks=blocks,
        )


def create_gcp_repository_from_env() -> GcpRepository:
    upstash_redis_rest_url = os.getenv("UPSTASH_REDIS_REST_URL")
    upstash_redis_rest_token = os.getenv("UPSTASH_REDIS_REST_TOKEN")

    if not upstash_redis_rest_url:
        raise RuntimeError("UPSTASH_REDIS_REST_URL must be set")
    if not upstash_redis_rest_token:
        raise RuntimeError("UPSTASH_REDIS_REST_TOKEN must be set")

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT must be set")

    firestore_database = os.getenv("FIRESTORE_DATABASE", "(default)")
    return GcpRepository(
        upstash_redis_rest_url=upstash_redis_rest_url,
        upstash_redis_rest_token=upstash_redis_rest_token,
        project_id=project_id,
        firestore_database=firestore_database,
    )
