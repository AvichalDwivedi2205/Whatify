from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import (
    AssetStatus,
    AssetType,
    DirectorSignalType,
    InterleavedTrigger,
    InterruptKind,
    Mode,
    UIActionType,
)
from app.utils.time import utc_now


class Choice(BaseModel):
    choice_id: str
    label: str
    consequence_hint: str


class BranchRuleUpdate(BaseModel):
    rule_id: str
    statement: str
    confidence: float = Field(default=0.75, ge=0, le=1)
    constraints: list[str] = Field(default_factory=list)


class BeatSpec(BaseModel):
    beat_id: str
    objective: str
    setup: str
    escalation: str
    act_title: str = "Act"
    act_time_label: str = "Unknown Era"
    narration_script: str = ""
    intermission_line: str = "Should we continue to the next act?"
    choices: list[Choice] = Field(min_length=2, max_length=4)
    consequence_seed: str
    transition_hook: str
    active_entities: list[str] = Field(default_factory=list)
    branch_rule_updates: list[BranchRuleUpdate] = Field(default_factory=list)


class ConsistencyFix(BaseModel):
    field: str
    replacement: str
    reason: str


class ConsistencyReport(BaseModel):
    ok: bool
    fixes: list[ConsistencyFix] = Field(default_factory=list)
    continuity_warnings: list[str] = Field(default_factory=list)


class RealityAnchorCard(BaseModel):
    title: str
    bullet: str
    citation: str | None = None


class ComparisonPoint(BaseModel):
    changed_fact: str
    real_fact: str


class RealityResponse(BaseModel):
    cards: list[RealityAnchorCard] = Field(default_factory=list)
    comparison_points: list[ComparisonPoint] = Field(default_factory=list)


class Shot(BaseModel):
    shot_id: str
    framing: str
    composition: str
    camera_motion: str
    prompt: str
    priority: Literal["high", "medium", "low"] = "medium"
    reuse_tags: list[str] = Field(default_factory=list)


class ShotPlan(BaseModel):
    shots: list[Shot] = Field(min_length=3, max_length=6)
    hero_shots: list[Shot] = Field(default_factory=list, max_length=2)


class InterleavedBlock(BaseModel):
    part_order: int = Field(ge=0)
    kind: Literal["text", "image"]
    text: str | None = None
    mime_type: str | None = None
    uri: str | None = None
    inline_data_b64: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> InterleavedBlock:
        if self.kind == "text":
            if not self.text:
                raise ValueError("text part requires text")
            return self

        if self.kind == "image":
            if not (self.uri or self.inline_data_b64):
                raise ValueError("image part requires uri or inline_data_b64")
            return self

        raise ValueError(f"unsupported interleaved part kind: {self.kind}")


class InterleavedGeneration(BaseModel):
    run_id: str
    session_id: str
    beat_id: str
    trigger: InterleavedTrigger
    model_id: str
    request_id: str
    ts: datetime = Field(default_factory=utc_now)
    blocks: list[InterleavedBlock] = Field(default_factory=list, min_length=1)


class OverlayNode(BaseModel):
    node_id: str
    label: str


class OverlayEdge(BaseModel):
    edge_id: str
    from_node: str
    to_node: str
    justification: str
    supporting_event_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0, le=1)


class ExplainResponse(BaseModel):
    spoken_answer: str
    overlay_chain: list[OverlayEdge] = Field(default_factory=list)
    groundedness_flags: dict[str, bool] = Field(default_factory=dict)


class DirectorSignal(BaseModel):
    signal_id: str
    type: DirectorSignalType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utc_now)


class UIAction(BaseModel):
    action_id: str
    type: UIActionType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utc_now)
    retry_count: int = 0


class MemoryWrite(BaseModel):
    kind: str
    session_id: str
    beat_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AssetJob(BaseModel):
    job_id: str
    type: AssetType
    priority: Literal["high", "normal", "low"] = "normal"
    session_id: str
    branch_id: str
    beat_id: str
    shot_id: str
    prompt: str
    style_ref: str = "style_bible_v1"
    deadline_ms: int = 10000


class EventLinks(BaseModel):
    asset_ids: list[str] = Field(default_factory=list)
    graph_edge_ids: list[str] = Field(default_factory=list)


class EventRecord(BaseModel):
    event_id: str
    ts: datetime = Field(default_factory=utc_now)
    session_id: str
    branch_id: str
    beat_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    links: EventLinks = Field(default_factory=EventLinks)


class StartSessionRequest(BaseModel):
    divergence_point: str | None = "What if the Library of Alexandria never burned?"
    tone: str = "cinematic"
    pacing: str = "normal"
    auto_run: bool = True


class BeginSessionRequest(BaseModel):
    divergence_point: str
    tone: str = "cinematic"
    pacing: str = "normal"


class StartSessionResponse(BaseModel):
    session_id: str
    branch_id: str
    beat_id: str
    stream_token: str


class InterruptRequest(BaseModel):
    kind: InterruptKind
    question: str | None = None


class AssetExplainRequest(BaseModel):
    question: str


class ChoiceRequest(BaseModel):
    choice_id: str


class AckRequest(BaseModel):
    action_id: str


class SessionStateResponse(BaseModel):
    session_id: str
    branch_id: str
    beat_id: str
    beat_index: int
    mode: Mode
    pacing: str
    video_budget_remaining: int
    pending_actions: int
    target_beats: int
    phase: str
    awaiting_continue: bool


class TimelineEdgeResponse(BaseModel):
    edge_id: str
    from_node: str
    to_node: str
    edge_type: str
    justification: str
    supporting_event_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.8


class TimelineResponse(BaseModel):
    session_id: str
    edges: list[TimelineEdgeResponse]


class InterleavedProofResponse(BaseModel):
    session_id: str
    beat_id: str | None = None
    run: InterleavedGeneration | None = None


class AssetCallbackRequest(BaseModel):
    asset_id: str
    session_id: str
    beat_id: str
    shot_id: str
    status: AssetStatus
    uri: str | None = None
    generation_time_ms: int | None = None


class ActionAckResponse(BaseModel):
    ok: bool


class GenericResponse(BaseModel):
    ok: bool
    message: str


class DirectorSignalRequest(BaseModel):
    type: DirectorSignalType
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def payload_size_guard(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > 4096:
            raise ValueError("payload too large")
        return value
