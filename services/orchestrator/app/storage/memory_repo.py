from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from app.models.contracts import BeatSpec, EventRecord
from app.models.state import (
    AssetRecord,
    BeatSummary,
    InterleavedRunRecord,
    SessionHotState,
    TimelineEdge,
)
from app.storage.repository import RepositoryProtocol


class InMemoryRepository(RepositoryProtocol):
    def __init__(self) -> None:
        self._sessions: dict[str, SessionHotState] = {}
        self._session_context: dict[str, dict[str, Any]] = defaultdict(dict)
        self._events: dict[str, list[EventRecord]] = defaultdict(list)
        self._beat_specs: dict[str, dict[str, BeatSpec]] = defaultdict(dict)
        self._beat_summaries: dict[str, list[BeatSummary]] = defaultdict(list)
        self._timeline_edges: dict[str, list[TimelineEdge]] = defaultdict(list)
        self._assets: dict[str, dict[str, AssetRecord]] = defaultdict(dict)
        self._interleaved_runs: dict[str, list[InterleavedRunRecord]] = defaultdict(list)
        self._action_seq: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def upsert_session(self, state: SessionHotState) -> None:
        async with self._lock:
            self._sessions[state.session_id] = state

    async def get_session(self, session_id: str) -> SessionHotState:
        async with self._lock:
            return self._sessions[session_id]

    async def set_session_context(self, session_id: str, data: dict[str, Any]) -> None:
        async with self._lock:
            self._session_context[session_id].update(data)

    async def get_session_context(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            return dict(self._session_context.get(session_id, {}))

    async def append_event(self, record: EventRecord) -> None:
        async with self._lock:
            self._events[record.session_id].append(record)

    async def list_events(self, session_id: str, limit: int | None = None) -> list[EventRecord]:
        async with self._lock:
            events = self._events.get(session_id, [])
            if limit is None:
                return list(events)
            return list(events[-limit:])

    async def set_beat_spec(self, session_id: str, beat_id: str, spec: BeatSpec) -> None:
        async with self._lock:
            self._beat_specs[session_id][beat_id] = spec

    async def get_beat_spec(self, session_id: str, beat_id: str) -> BeatSpec | None:
        async with self._lock:
            return self._beat_specs.get(session_id, {}).get(beat_id)

    async def recent_beat_summaries(self, session_id: str, limit: int = 2) -> list[BeatSummary]:
        async with self._lock:
            summaries = self._beat_summaries.get(session_id, [])
            return list(summaries[-limit:])

    async def add_beat_summary(self, session_id: str, summary: BeatSummary) -> None:
        async with self._lock:
            self._beat_summaries[session_id].append(summary)

    async def add_timeline_edge(self, session_id: str, edge: TimelineEdge) -> None:
        async with self._lock:
            self._timeline_edges[session_id].append(edge)

    async def list_timeline_edges(self, session_id: str) -> list[TimelineEdge]:
        async with self._lock:
            return list(self._timeline_edges.get(session_id, []))

    async def upsert_asset(self, session_id: str, record: AssetRecord) -> None:
        async with self._lock:
            self._assets[session_id][record.asset_id] = record

    async def get_asset(self, session_id: str, asset_id: str) -> AssetRecord | None:
        async with self._lock:
            return self._assets.get(session_id, {}).get(asset_id)

    async def list_assets_for_beat(
        self,
        session_id: str,
        beat_id: str,
        *,
        asset_type: str | None = None,
        status: str | None = None,
    ) -> list[AssetRecord]:
        async with self._lock:
            assets = list(self._assets.get(session_id, {}).values())

        filtered = [asset for asset in assets if asset.beat_id == beat_id]
        if asset_type is not None:
            filtered = [asset for asset in filtered if asset.type.value == asset_type]
        if status is not None:
            filtered = [asset for asset in filtered if asset.status.value == status]
        filtered.sort(key=lambda asset: (asset.shot_id, asset.asset_id))
        return filtered

    async def upsert_interleaved_run(self, session_id: str, record: InterleavedRunRecord) -> None:
        async with self._lock:
            runs = self._interleaved_runs[session_id]
            for index, item in enumerate(runs):
                if item.run_id == record.run_id:
                    runs[index] = record
                    break
            else:
                runs.append(record)
            runs.sort(key=lambda run: run.created_ts)

    async def get_latest_interleaved_run(
        self,
        session_id: str,
        beat_id: str | None = None,
    ) -> InterleavedRunRecord | None:
        async with self._lock:
            runs = self._interleaved_runs.get(session_id, [])
            if not runs:
                return None

            if beat_id is None:
                return runs[-1]

            for run in reversed(runs):
                if run.beat_id == beat_id:
                    return run
            return None

    async def next_action_seq(self, session_id: str) -> int:
        async with self._lock:
            self._action_seq[session_id] += 1
            return self._action_seq[session_id]
