from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import AssetStatus, AssetType, Mode


@dataclass(slots=True)
class SessionHotState:
    session_id: str
    branch_id: str
    beat_id: str
    beat_index: int
    mode: Mode
    pacing: str
    video_budget_remaining: int
    target_beats: int = 6
    current_phase: str = "onboarding"
    awaiting_continue: bool = False
    prefetch_queue_status: str = "idle"
    active_entities: list[str] = field(default_factory=list)
    pending_actions: list[str] = field(default_factory=list)
    user_tone: str = "cinematic"


@dataclass(slots=True)
class BeatSummary:
    beat_id: str
    summary_5_lines: list[str]
    new_facts: list[str]
    new_entities: list[str]
    causal_delta: str
    open_threads: list[str]
    asset_refs: list[str]


@dataclass(slots=True)
class TimelineEdge:
    edge_id: str
    from_node: str
    to_node: str
    edge_type: str
    justification: str
    supporting_event_ids: list[str]
    confidence: float


@dataclass(slots=True)
class AssetRecord:
    asset_id: str
    type: AssetType
    session_id: str
    branch_id: str
    beat_id: str
    shot_id: str
    prompt_hash: str
    status: AssetStatus
    uri: str | None = None
    reuse_tags: list[str] = field(default_factory=list)
    cost_estimate: float | None = None
    generation_time_ms: int | None = None


@dataclass(slots=True)
class InterleavedBlockRecord:
    part_order: int
    kind: str
    text: str | None = None
    mime_type: str | None = None
    uri: str | None = None
    inline_data_b64: str | None = None


@dataclass(slots=True)
class InterleavedRunRecord:
    run_id: str
    session_id: str
    beat_id: str
    trigger: str
    model_id: str
    request_id: str
    created_ts: str
    blocks: list[InterleavedBlockRecord] = field(default_factory=list)
