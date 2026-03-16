from __future__ import annotations

from typing import Any, Protocol

from app.models.contracts import BeatSpec, EventRecord
from app.models.state import (
    AssetRecord,
    BeatSummary,
    InterleavedRunRecord,
    SessionHotState,
    TimelineEdge,
)


class RepositoryProtocol(Protocol):
    async def upsert_session(self, state: SessionHotState) -> None: ...

    async def get_session(self, session_id: str) -> SessionHotState: ...

    async def set_session_context(self, session_id: str, data: dict[str, Any]) -> None: ...

    async def get_session_context(self, session_id: str) -> dict[str, Any]: ...

    async def append_event(self, record: EventRecord) -> None: ...

    async def list_events(self, session_id: str, limit: int | None = None) -> list[EventRecord]: ...

    async def set_beat_spec(self, session_id: str, beat_id: str, spec: BeatSpec) -> None: ...

    async def get_beat_spec(self, session_id: str, beat_id: str) -> BeatSpec | None: ...

    async def recent_beat_summaries(self, session_id: str, limit: int = 2) -> list[BeatSummary]: ...

    async def add_beat_summary(self, session_id: str, summary: BeatSummary) -> None: ...

    async def add_timeline_edge(self, session_id: str, edge: TimelineEdge) -> None: ...

    async def list_timeline_edges(self, session_id: str) -> list[TimelineEdge]: ...

    async def upsert_asset(self, session_id: str, record: AssetRecord) -> None: ...

    async def get_asset(self, session_id: str, asset_id: str) -> AssetRecord | None: ...

    async def list_assets_for_beat(
        self,
        session_id: str,
        beat_id: str,
        *,
        asset_type: str | None = None,
        status: str | None = None,
    ) -> list[AssetRecord]: ...

    async def upsert_interleaved_run(self, session_id: str, record: InterleavedRunRecord) -> None: ...

    async def get_latest_interleaved_run(
        self,
        session_id: str,
        beat_id: str | None = None,
    ) -> InterleavedRunRecord | None: ...

    async def next_action_seq(self, session_id: str) -> int: ...
