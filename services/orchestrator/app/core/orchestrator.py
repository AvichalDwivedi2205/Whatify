from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status

from app.core.agent_runtime import AgentRuntimeProtocol
from app.core.historian_grounding import apply_historian_grounding
from app.core.state_machine import InvalidTransition, TransitionCommand, apply_transition
from app.core.working_memory import build_working_memory
from app.models.contracts import (
    AckRequest,
    ActionAckResponse,
    AssetCallbackRequest,
    AssetExplainRequest,
    AssetJob,
    BeatSpec,
    BeginSessionRequest,
    ChoiceRequest,
    DirectorSignalRequest,
    EventLinks,
    EventRecord,
    GenericResponse,
    InterleavedGeneration,
    InterleavedProofResponse,
    InterruptRequest,
    SessionStateResponse,
    ShotPlan,
    StartSessionRequest,
    StartSessionResponse,
    TimelineEdgeResponse,
    TimelineResponse,
    UIAction,
    VisualAssetFrame,
    VisualStateResponse,
)
from app.models.enums import (
    AssetStatus,
    AssetType,
    EventType,
    InterleavedTrigger,
    InterruptKind,
    Mode,
    UIActionType,
)
from app.models.state import AssetRecord, BeatSummary, InterleavedBlockRecord, InterleavedRunRecord, SessionHotState
from app.queue.dispatcher import AssetDispatcherProtocol
from app.storage.repository import RepositoryProtocol
from app.streams.action_bus import ActionBus
from app.streams.caption_bus import CaptionBus
from app.utils.id import hash_prompt, stable_id
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


class OrchestratorService:
    def __init__(
        self,
        repo: RepositoryProtocol,
        action_bus: ActionBus,
        caption_bus: CaptionBus,
        agents: AgentRuntimeProtocol,
        dispatcher: AssetDispatcherProtocol,
        orchestrator_callback_url: str,
        max_beats: int = 8,
    ) -> None:
        self.repo = repo
        self.action_bus = action_bus
        self.caption_bus = caption_bus
        self.agents = agents
        self.dispatcher = dispatcher
        self.orchestrator_callback_url = orchestrator_callback_url
        self.max_beats = max_beats
        self.video_peak_beats = {2, 4, 6}
        self.interleaved_max_attempts = 4
        self.interleaved_retry_base_delay_seconds = 2.0
        self._interleaved_generations: dict[tuple[str, str], InterleavedGeneration] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def start_session(self, request: StartSessionRequest) -> StartSessionResponse:
        raw_divergence_point = (request.divergence_point or "").strip()
        if request.auto_run:
            divergence_point = raw_divergence_point or "What if the Library of Alexandria never burned?"
        else:
            divergence_point = raw_divergence_point

        session_id = stable_id("s", f"{divergence_point}:{utc_now_iso()}")
        branch_id = stable_id("b", session_id)
        target_beats = self._compute_target_beats(divergence_point or "What if history took a different turn?")
        state = SessionHotState(
            session_id=session_id,
            branch_id=branch_id,
            beat_id="beat_1",
            beat_index=1,
            mode=Mode.STORY if request.auto_run else Mode.ONBOARDING,
            pacing=request.pacing,
            video_budget_remaining=target_beats,
            target_beats=target_beats,
            current_phase="story" if request.auto_run else "onboarding",
            awaiting_continue=False,
            user_tone=request.tone,
        )
        await self.repo.upsert_session(state)
        await self.repo.set_session_context(
            session_id,
            {
                "divergence_point": divergence_point,
                "branch_rules": [],
                "track": "creative-storyteller",
                "scene_snapshot": {},
            },
        )
        await self._append_event(
            state=state,
            event_type=EventType.SESSION_STARTED,
            payload={"divergence_point": divergence_point, "auto_run": request.auto_run, "target_beats": target_beats},
        )
        logger.info(
            "session started session_id=%s branch_id=%s auto_run=%s target_beats=%s",
            session_id,
            branch_id,
            request.auto_run,
            target_beats,
        )

        if request.auto_run:
            await self._prepare_story_outline(
                state=state,
                divergence_point=divergence_point or "What if the Library of Alexandria never burned?",
            )
            await self._run_beat(state)
        else:
            await self._emit_action(state, UIActionType.SET_MODE, {"mode": Mode.ONBOARDING.value})
            await self._emit_action(
                state,
                UIActionType.CAPTION_APPEND,
                {"text": "Session ready. Describe your divergence and say begin when ready."},
            )
        return StartSessionResponse(
            session_id=session_id,
            branch_id=branch_id,
            beat_id=state.beat_id,
            stream_token=stable_id("stream", session_id),
        )

    async def begin_session(self, session_id: str, request: BeginSessionRequest) -> GenericResponse:
        state = await self._must_get_session(session_id)
        divergence_point = request.divergence_point.strip()
        if not divergence_point:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="divergence_point is required")

        if state.mode != Mode.ONBOARDING:
            return GenericResponse(ok=True, message="session already running")

        state.user_tone = request.tone
        state.pacing = request.pacing
        try:
            state.mode = apply_transition(state.mode, TransitionCommand.BEGIN_SESSION)
        except InvalidTransition as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        state.beat_index = 1
        state.beat_id = "beat_1"
        state.target_beats = self._compute_target_beats(divergence_point)
        state.video_budget_remaining = state.target_beats
        state.awaiting_continue = False
        state.current_phase = "processing"

        await self.repo.set_session_context(
            session_id,
            {
                "divergence_point": divergence_point,
                "branch_rules": [],
            },
        )
        await self.repo.upsert_session(state)
        await self._append_event(
            state=state,
            event_type=EventType.SESSION_STARTED,
            payload={"divergence_point": divergence_point, "auto_run": True, "target_beats": state.target_beats},
        )
        logger.info(
            "begin session session_id=%s tone=%s pacing=%s target_beats=%s",
            session_id,
            request.tone,
            request.pacing,
            state.target_beats,
        )
        await self._prepare_story_outline(state=state, divergence_point=divergence_point)
        await self._run_beat(state)
        return GenericResponse(ok=True, message="session begun")

    async def continue_session(self, session_id: str) -> GenericResponse:
        state = await self._must_get_session(session_id)
        logger.info(
            "continue requested session_id=%s mode=%s beat_index=%s",
            session_id,
            state.mode.value,
            state.beat_index,
        )

        if state.mode == Mode.COMPLETE:
            return GenericResponse(ok=True, message="story complete")

        if state.mode == Mode.NAV:
            state.mode = apply_transition(state.mode, TransitionCommand.CONTINUE_STORY)
            state.current_phase = "story"
            await self.repo.upsert_session(state)
            await self._emit_action(state, UIActionType.SET_MODE, {"mode": Mode.STORY.value})
            await self._emit_action(state, UIActionType.CAPTION_APPEND, {"text": "Resuming the current act."})
            return GenericResponse(ok=True, message="resumed")

        if state.beat_index >= state.target_beats:
            return await self._mark_story_complete(state)

        if state.mode not in {Mode.STORY, Mode.INTERMISSION, Mode.CHOICE}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session is not ready to continue")

        try:
            state.mode = apply_transition(state.mode, TransitionCommand.CONTINUE_STORY)
        except InvalidTransition:
            state.mode = Mode.STORY
        state.awaiting_continue = False
        state.current_phase = "processing"
        state.beat_index += 1
        state.beat_id = f"beat_{state.beat_index}"
        await self.repo.upsert_session(state)
        await self._run_beat(state)
        return GenericResponse(ok=True, message="continued")

    async def choose(self, session_id: str, request: ChoiceRequest) -> GenericResponse:
        state = await self._must_get_session(session_id)
        beat_spec = await self.repo.get_beat_spec(session_id, state.beat_id)
        if beat_spec is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="beat not found")

        choice = next((item for item in beat_spec.choices if item.choice_id == request.choice_id), None)
        if choice is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid choice")
        logger.info(
            "choice selected session_id=%s beat_id=%s choice_id=%s",
            session_id,
            state.beat_id,
            choice.choice_id,
        )

        await self._append_event(
            state=state,
            event_type=EventType.CHOICE_SELECTED,
            payload={"choice_id": choice.choice_id, "label": choice.label},
        )

        events = await self.repo.list_events(session_id, limit=4)
        edge = await self.agents.make_edge(
            beat_id=state.beat_id,
            choice_id=choice.choice_id,
            event_ids=[event.event_id for event in events],
        )
        await self.repo.add_timeline_edge(session_id, edge)
        await self._append_event(
            state=state,
            event_type=EventType.GRAPH_EDGE_ADDED,
            payload={"edge_id": edge.edge_id, "to": edge.to_node},
            links=EventLinks(graph_edge_ids=[edge.edge_id]),
        )

        summary = self._build_beat_summary(beat_spec, choice.choice_id)
        await self.repo.add_beat_summary(session_id, summary)
        await self._append_event(
            state=state,
            event_type=EventType.BEAT_ENDED,
            payload={"beat_index": state.beat_index, "choice_id": choice.choice_id},
        )

        if state.beat_index >= state.target_beats:
            return await self._mark_story_complete(
                state,
                raise_on_invalid_transition=True,
                completion_caption=(
                    "Final act complete. Ask WHY/COMPARE/REWIND for post-scene analysis or start a new branch."
                ),
            )

        transition_command = (
            TransitionCommand.USER_CHOICE
            if state.mode == Mode.CHOICE
            else TransitionCommand.ENTER_INTERMISSION
        )
        try:
            state.mode = apply_transition(state.mode, transition_command)
        except InvalidTransition as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        state.awaiting_continue = True
        state.current_phase = "intermission"
        await self.repo.upsert_session(state)
        await self._emit_action(state, UIActionType.SET_MODE, {"mode": Mode.INTERMISSION.value})
        await self._emit_action(
            state,
            UIActionType.SHOW_INTERMISSION,
            {
                "beat_index": state.beat_index,
                "next_beat_index": state.beat_index + 1,
                "target_beats": state.target_beats,
                "prompt": beat_spec.intermission_line,
            },
        )
        await self._emit_action(
            state,
            UIActionType.CAPTION_APPEND,
            {"text": beat_spec.intermission_line},
        )
        return GenericResponse(ok=True, message="choice accepted; waiting for continue")

    async def interrupt(self, session_id: str, request: InterruptRequest) -> GenericResponse:
        state = await self._must_get_session(session_id)
        logger.info(
            "interrupt requested session_id=%s mode=%s kind=%s question=%s",
            session_id,
            state.mode.value,
            request.kind.value,
            (request.question or "")[:160],
        )

        if request.kind == InterruptKind.PAUSE:
            state.mode = Mode.NAV
            await self.repo.upsert_session(state)
            await self._append_event(
                state=state,
                event_type=EventType.USER_INTERRUPT,
                payload={"kind": request.kind.value},
            )
            await self._emit_action(state, UIActionType.SET_MODE, {"mode": state.mode.value})
            await self._emit_action(
                state,
                UIActionType.CAPTION_APPEND,
                {"text": "Paused. Say continue to resume story flow."},
            )
            return GenericResponse(ok=True, message="paused")

        if request.kind == InterruptKind.CHANGE_TONE:
            next_tone = (request.question or "cinematic").strip()
            if not next_tone:
                next_tone = "cinematic"
            state.user_tone = next_tone[:64]
            await self.repo.upsert_session(state)
            await self._append_event(
                state=state,
                event_type=EventType.USER_INTERRUPT,
                payload={"kind": request.kind.value, "tone": state.user_tone},
            )
            await self._emit_action(
                state,
                UIActionType.CAPTION_APPEND,
                {"text": f"Tone shifted to {state.user_tone}. Continuing scene."},
            )
            return GenericResponse(ok=True, message="tone updated")

        prior_mode = state.mode
        try:
            state.mode = apply_transition(state.mode, TransitionCommand.ENTER_EXPLAIN)
        except InvalidTransition as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        await self.repo.upsert_session(state)
        await self._append_event(
            state=state,
            event_type=EventType.USER_INTERRUPT,
            payload={"kind": request.kind.value, "question": request.question},
        )

        edges = await self.repo.list_timeline_edges(session_id)
        chain = edges[-6:]
        beat_context = f"Current beat: {state.beat_id}"

        if request.kind == InterruptKind.COMPARE:
            topic = request.question or "current divergence"
            reality = await self.agents.reality_compare(topic)
            reality = apply_historian_grounding(topic=topic, reality=reality)
            await self._emit_action(
                state,
                UIActionType.SHOW_REALITY_ANCHOR,
                reality.model_dump(mode="json"),
            )
            beat_spec = await self.repo.get_beat_spec(session_id, state.beat_id)
            if beat_spec is not None:
                await self._generate_interleaved_for_beat(
                    session_id=state.session_id,
                    branch_id=state.branch_id,
                    beat_id=state.beat_id,
                    beat_index=state.beat_index,
                    beat_spec=beat_spec,
                    trigger=InterleavedTrigger.COMPARE,
                    question=request.question,
                )
            await self._append_event(
                state=state,
                event_type=EventType.COMPARE_ANSWERED,
                payload={
                    "topic": topic,
                    "citation_count": len([card for card in reality.cards if card.citation]),
                    "citations": [card.citation for card in reality.cards if card.citation],
                },
            )
            answer_text = "Comparison overlay loaded against real history anchors."
        else:
            explain = await self.agents.explain(
                question=request.question or request.kind.value,
                chain=chain,
                beat_context=beat_context,
            )
            await self._emit_action(
                state,
                UIActionType.SHOW_EXPLAIN_OVERLAY,
                {
                    "chain": [edge.model_dump(mode="json") for edge in explain.overlay_chain],
                    "groundedness_flags": explain.groundedness_flags,
                },
            )
            answer_text = explain.spoken_answer
            await self._append_event(
                state=state,
                event_type=EventType.EXPLAIN_ANSWERED,
                payload={"question": request.question or request.kind.value},
            )

        await self._emit_action(state, UIActionType.SET_MODE, {"mode": Mode.EXPLAIN.value})
        await self._emit_action(state, UIActionType.CAPTION_APPEND, {"text": answer_text})

        await self._restore_mode_after_interrupt(session_id=session_id, state=state, prior_mode=prior_mode)

        return GenericResponse(ok=True, message="interrupt handled")

    async def _mark_story_complete(
        self,
        state: SessionHotState,
        *,
        raise_on_invalid_transition: bool = False,
        completion_caption: str | None = None,
    ) -> GenericResponse:
        try:
            state.mode = apply_transition(state.mode, TransitionCommand.MARK_COMPLETE)
        except InvalidTransition as exc:
            if raise_on_invalid_transition:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            state.mode = Mode.COMPLETE

        state.current_phase = "complete"
        state.awaiting_continue = False
        await self.repo.upsert_session(state)
        await self._emit_action(state, UIActionType.SET_MODE, {"mode": Mode.COMPLETE.value})
        if completion_caption:
            await self._emit_action(state, UIActionType.CAPTION_APPEND, {"text": completion_caption})
        return GenericResponse(ok=True, message="story complete")

    async def _restore_mode_after_interrupt(
        self,
        *,
        session_id: str,
        state: SessionHotState,
        prior_mode: Mode,
    ) -> None:
        if prior_mode == Mode.CHOICE:
            state.mode = Mode.CHOICE
            await self.repo.upsert_session(state)
            beat_spec = await self.repo.get_beat_spec(session_id, state.beat_id)
            if beat_spec is not None:
                await self._emit_action(
                    state,
                    UIActionType.SHOW_CHOICES,
                    {"choices": [choice.model_dump(mode="json") for choice in beat_spec.choices]},
                )
            return

        restored_mode = prior_mode if prior_mode in {Mode.INTERMISSION, Mode.ONBOARDING, Mode.COMPLETE} else Mode.STORY
        state.mode = restored_mode
        await self.repo.upsert_session(state)
        await self._emit_action(state, UIActionType.SET_MODE, {"mode": restored_mode.value})

    async def asset_explain(self, session_id: str, request: AssetExplainRequest) -> GenericResponse:
        state = await self._must_get_session(session_id)
        question = request.question.strip()
        if not question:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="question is required")

        context = await self.repo.get_session_context(session_id)
        snapshot = context.get("scene_snapshot", {})
        snapshot_text = json.dumps(snapshot, ensure_ascii=True)[:1500]
        edges = await self.repo.list_timeline_edges(session_id)

        explain = await self.agents.explain(
            question=question,
            chain=edges[-6:],
            beat_context=f"Current beat: {state.beat_id}. On-screen snapshot: {snapshot_text}",
        )

        await self._emit_action(
            state,
            UIActionType.SHOW_EXPLAIN_OVERLAY,
            {
                "chain": [edge.model_dump(mode="json") for edge in explain.overlay_chain],
                "groundedness_flags": explain.groundedness_flags,
            },
        )
        await self._emit_action(state, UIActionType.CAPTION_APPEND, {"text": explain.spoken_answer})
        return GenericResponse(ok=True, message=explain.spoken_answer)

    async def ack_action(self, session_id: str, request: AckRequest) -> ActionAckResponse:
        state = await self._must_get_session(session_id)
        removed = await self.action_bus.ack(session_id, request.action_id)
        if removed and request.action_id in state.pending_actions:
            state.pending_actions.remove(request.action_id)
            await self.repo.upsert_session(state)
            await self._append_event(
                state=state,
                event_type=EventType.UI_ACTION_ACKED,
                payload={"action_id": request.action_id},
            )
        return ActionAckResponse(ok=removed)

    async def update_scene_snapshot(self, session_id: str, snapshot: dict[str, Any]) -> None:
        await self._must_get_session(session_id)
        await self.repo.set_session_context(session_id, {"scene_snapshot": snapshot})

    async def get_state(self, session_id: str) -> SessionStateResponse:
        state = await self._must_get_session(session_id)
        pending_count = await self.action_bus.pending_count(session_id)
        return SessionStateResponse(
            session_id=state.session_id,
            branch_id=state.branch_id,
            beat_id=state.beat_id,
            beat_index=state.beat_index,
            mode=state.mode,
            pacing=state.pacing,
            video_budget_remaining=state.video_budget_remaining,
            pending_actions=pending_count,
            target_beats=state.target_beats,
            phase=state.current_phase,
            awaiting_continue=state.awaiting_continue,
        )

    async def get_timeline(self, session_id: str) -> TimelineResponse:
        await self._must_get_session(session_id)
        edges = await self.repo.list_timeline_edges(session_id)
        return TimelineResponse(
            session_id=session_id,
            edges=[
                TimelineEdgeResponse(
                    edge_id=edge.edge_id,
                    from_node=edge.from_node,
                    to_node=edge.to_node,
                    edge_type=edge.edge_type,
                    justification=edge.justification,
                    supporting_event_ids=edge.supporting_event_ids,
                    confidence=edge.confidence,
                )
                for edge in edges
            ],
        )

    async def get_interleaved_proof(
        self,
        session_id: str,
        beat_id: str | None = None,
    ) -> InterleavedProofResponse:
        await self._must_get_session(session_id)
        run = await self.repo.get_latest_interleaved_run(session_id, beat_id=beat_id)
        if run is None:
            return InterleavedProofResponse(session_id=session_id, beat_id=beat_id, run=None)

        cached = self._interleaved_generations.get((session_id, run.beat_id))
        if cached is not None and cached.run_id == run.run_id:
            return InterleavedProofResponse(session_id=session_id, beat_id=run.beat_id, run=cached)

        return InterleavedProofResponse(
            session_id=session_id,
            beat_id=run.beat_id,
            run=InterleavedGeneration(
                run_id=run.run_id,
                session_id=run.session_id,
                beat_id=run.beat_id,
                trigger=(
                    InterleavedTrigger(run.trigger)
                    if run.trigger in {item.value for item in InterleavedTrigger}
                    else InterleavedTrigger.BEAT_START
                ),
                model_id=run.model_id,
                request_id=run.request_id,
                ts=self._parse_datetime(run.created_ts),
                blocks=[
                    {
                        "part_order": block.part_order,
                        "kind": block.kind,
                        "text": block.text,
                        "mime_type": block.mime_type,
                        "uri": self._proof_image_uri(run.run_id, block),
                        "inline_data_b64": block.inline_data_b64,
                    }
                    for block in run.blocks
                ],
            ),
        )

    async def get_visual_state(self, session_id: str, beat_id: str) -> VisualStateResponse:
        await self._must_get_session(session_id)
        proof = await self.get_interleaved_proof(session_id=session_id, beat_id=beat_id)
        storyboard_assets = await self.repo.list_assets_for_beat(
            session_id,
            beat_id,
            asset_type=AssetType.STORYBOARD.value,
            status=AssetStatus.READY.value,
        )
        hero_assets = await self.repo.list_assets_for_beat(
            session_id,
            beat_id,
            asset_type=AssetType.HERO_VIDEO.value,
            status=AssetStatus.READY.value,
        )
        return VisualStateResponse(
            session_id=session_id,
            beat_id=beat_id,
            storyboard_frames=[
                VisualAssetFrame(
                    asset_id=asset.asset_id,
                    shot_id=asset.shot_id,
                    uri=asset.uri,
                    ready=True,
                )
                for asset in storyboard_assets
                if asset.uri
            ],
            hero_video_uri=hero_assets[0].uri if hero_assets else None,
            interleaved_run=proof.run,
        )

    async def handle_director_signal(self, session_id: str, request: DirectorSignalRequest) -> GenericResponse:
        state = await self._must_get_session(session_id)
        logger.info(
            "director signal session_id=%s type=%s payload_keys=%s",
            session_id,
            request.type.value,
            sorted(request.payload.keys()),
        )
        if request.type.value == "STORY_BRIEF_CAPTURED":
            divergence_point = str(request.payload.get("divergence_point", "")).strip()
            tone = str(request.payload.get("tone", state.user_tone))
            pacing = str(request.payload.get("pacing", state.pacing))
            if not divergence_point:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing divergence_point")
            return await self.begin_session(
                session_id,
                BeginSessionRequest(
                    divergence_point=divergence_point,
                    tone=tone,
                    pacing=pacing,
                ),
            )
        if request.type.value == "CONTINUE_ACT":
            return await self.continue_session(session_id)
        if request.type.value == "INTERRUPT":
            raw_kind = str(request.payload.get("kind", InterruptKind.WHY.value)).upper()
            try:
                interrupt_kind = InterruptKind(raw_kind)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"invalid interrupt kind: {raw_kind}",
                ) from exc
            question = request.payload.get("question")
            if question is not None and not isinstance(question, str):
                question = str(question)
            return await self.interrupt(
                session_id,
                InterruptRequest(kind=interrupt_kind, question=question),
            )
        if request.type.value == "READY_FOR_CHOICES":
            state.mode = apply_transition(state.mode, TransitionCommand.READY_FOR_CHOICES)
            await self.repo.upsert_session(state)
            beat_spec = await self.repo.get_beat_spec(session_id, state.beat_id)
            if beat_spec is not None:
                await self._show_choices(state, beat_spec)
            return GenericResponse(ok=True, message="choices emitted")
        if request.type.value == "PACE_HINT":
            pace = str(request.payload.get("pace", request.payload.get("pacing", state.pacing)))
            state.pacing = pace
            await self.repo.upsert_session(state)
            return GenericResponse(ok=True, message="pace updated")
        if request.type.value == "EMOTION_TARGET":
            target = str(request.payload.get("target", "neutral"))
            await self.repo.set_session_context(session_id, {"emotion_target": target})
            return GenericResponse(ok=True, message="emotion target updated")
        return GenericResponse(ok=True, message="signal accepted")

    async def asset_callback(self, request: AssetCallbackRequest) -> GenericResponse:
        state = await self._must_get_session(request.session_id)
        logger.info(
            "asset callback session_id=%s beat_id=%s shot_id=%s asset_id=%s status=%s uri=%s duration_ms=%s",
            request.session_id,
            request.beat_id,
            request.shot_id,
            request.asset_id,
            request.status.value,
            request.uri,
            request.generation_time_ms,
        )
        record = await self.repo.get_asset(request.session_id, request.asset_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")

        if (
            record.status == request.status
            and record.uri == request.uri
            and record.generation_time_ms == request.generation_time_ms
        ):
            return GenericResponse(ok=True, message="duplicate callback ignored")

        if record.status == AssetStatus.READY and request.status == AssetStatus.READY and record.uri:
            return GenericResponse(ok=True, message="asset already ready")

        record.status = request.status
        record.uri = request.uri
        record.generation_time_ms = request.generation_time_ms
        await self.repo.upsert_asset(request.session_id, record)

        if request.status == AssetStatus.READY:
            if record.type == AssetType.STORYBOARD:
                await self._append_event_by_scope(
                    session_id=state.session_id,
                    branch_id=state.branch_id,
                    beat_id=record.beat_id,
                    event_type=EventType.STORYBOARD_READY,
                    payload={"asset_id": record.asset_id, "shot_id": record.shot_id},
                    links=EventLinks(asset_ids=[record.asset_id]),
                )
                await self._emit_action(
                    state,
                    UIActionType.SHOW_STORYBOARD,
                    {
                        "beat_id": record.beat_id,
                        "frames": [
                            {
                                "asset_id": record.asset_id,
                                "shot_id": record.shot_id,
                                "uri": record.uri,
                                "ready": True,
                            }
                        ]
                    },
                )
            elif record.type == AssetType.HERO_VIDEO:
                await self._append_event_by_scope(
                    session_id=state.session_id,
                    branch_id=state.branch_id,
                    beat_id=record.beat_id,
                    event_type=EventType.VIDEO_READY,
                    payload={"asset_id": record.asset_id, "shot_id": record.shot_id},
                    links=EventLinks(asset_ids=[record.asset_id]),
                )
                await self._emit_action(
                    state,
                    UIActionType.PLAY_VIDEO,
                    {
                        "beat_id": record.beat_id,
                        "uri": record.uri,
                        "shot_id": record.shot_id,
                        "asset_id": record.asset_id,
                    },
                )

        return GenericResponse(ok=True, message="asset callback processed")

    async def _run_beat(self, state: SessionHotState) -> None:
        state.current_phase = "processing"
        state.awaiting_continue = False
        await self.repo.upsert_session(state)
        logger.info(
            "run beat started session_id=%s beat_id=%s beat_index=%s",
            state.session_id,
            state.beat_id,
            state.beat_index,
        )

        beat_spec = await self.repo.get_beat_spec(state.session_id, state.beat_id)
        if beat_spec is None:
            context = await build_working_memory(self.repo, state.session_id)
            beat_spec = await self.agents.plan_beat(
                session_id=state.session_id,
                beat_id=state.beat_id,
                beat_index=state.beat_index,
                context=context,
            )
            beat_spec = await self._sanitize_beat_spec(beat_spec)
            await self.repo.set_beat_spec(state.session_id, state.beat_id, beat_spec)

        shot_plan = await self._load_or_plan_shot_plan(
            session_id=state.session_id,
            beat_id=state.beat_id,
            beat_spec=beat_spec,
        )
        logger.info(
            "beat planned session_id=%s beat_id=%s choices=%s shots=%s hero_shots=%s",
            state.session_id,
            state.beat_id,
            len(beat_spec.choices),
            len(shot_plan.shots),
            len(shot_plan.hero_shots),
        )

        context_update = await self.repo.get_session_context(state.session_id)
        existing_rules = context_update.get("branch_rules", [])
        existing_rules.extend([update.model_dump(mode="json") for update in beat_spec.branch_rule_updates])
        await self.repo.set_session_context(state.session_id, {"branch_rules": existing_rules[-12:]})

        state.active_entities = beat_spec.active_entities
        await self.repo.upsert_session(state)

        await self._append_event(
            state=state,
            event_type=EventType.BEAT_STARTED,
            payload={"beat_index": state.beat_index, "objective": beat_spec.objective},
        )
        await self._append_event(
            state=state,
            event_type=EventType.SHOTPLAN_CREATED,
            payload={"shots": len(shot_plan.shots), "hero_shots": len(shot_plan.hero_shots)},
        )

        state.mode = Mode.STORY
        state.current_phase = "story"
        state.awaiting_continue = state.beat_index < state.target_beats
        await self.repo.upsert_session(state)
        await self._emit_action(state, UIActionType.SET_MODE, {"mode": Mode.STORY.value})
        await self._emit_action(
            state,
            UIActionType.SHOW_ACT_REVEAL,
            {
                "beat_id": beat_spec.beat_id,
                "beat_index": state.beat_index,
                "target_beats": state.target_beats,
                "act_title": beat_spec.act_title,
                "act_time_label": beat_spec.act_time_label,
            },
        )
        await self._emit_action(
            state,
            UIActionType.SET_SCENE,
            {
                "beat_id": beat_spec.beat_id,
                "title": beat_spec.act_title or f"Scene {state.beat_index}",
                "setup": beat_spec.setup,
                "escalation": beat_spec.escalation,
                "act_time_label": beat_spec.act_time_label,
                "narration_script": beat_spec.narration_script,
            },
        )
        self._track_task(
            asyncio.create_task(
                self._generate_interleaved_for_beat(
                    session_id=state.session_id,
                    branch_id=state.branch_id,
                    beat_id=state.beat_id,
                    beat_index=state.beat_index,
                    beat_spec=beat_spec,
                    trigger=InterleavedTrigger.BEAT_START,
                    question=None,
                )
            )
        )
        await self._emit_action(
            state,
            UIActionType.SHOW_STORYBOARD,
            {
                "beat_id": state.beat_id,
                "frames": [
                    {"shot_id": shot.shot_id, "ready": False, "uri": None}
                    for shot in shot_plan.shots
                ]
            },
        )
        await self._dispatch_asset_jobs(state, beat_id=state.beat_id, shot_plan=shot_plan)
        if state.beat_index < state.target_beats:
            self._track_task(asyncio.create_task(self._prefetch_next_beat(state)))
        logger.info(
            "run beat ready session_id=%s beat_id=%s mode=%s",
            state.session_id,
            state.beat_id,
            state.mode.value,
        )

    async def _show_choices(self, state: SessionHotState, beat_spec: BeatSpec) -> None:
        await self._append_event(
            state=state,
            event_type=EventType.CHOICES_SHOWN,
            payload={"choice_count": len(beat_spec.choices)},
        )
        await self._emit_action(
            state,
            UIActionType.SHOW_CHOICES,
            {"choices": [choice.model_dump(mode="json") for choice in beat_spec.choices]},
        )

    async def _dispatch_asset_jobs(self, state: SessionHotState, *, beat_id: str, shot_plan: ShotPlan) -> None:
        for shot in shot_plan.shots:
            asset_id = stable_id("asset", f"{state.session_id}:{beat_id}:{shot.shot_id}:img")
            existing = await self.repo.get_asset(state.session_id, asset_id)
            if existing is not None:
                continue
            job = AssetJob(
                job_id=stable_id("job", asset_id),
                type=AssetType.STORYBOARD,
                session_id=state.session_id,
                branch_id=state.branch_id,
                beat_id=beat_id,
                shot_id=shot.shot_id,
                prompt=shot.prompt,
            )
            await self.repo.upsert_asset(
                state.session_id,
                AssetRecord(
                    asset_id=asset_id,
                    type=AssetType.STORYBOARD,
                    session_id=state.session_id,
                    branch_id=state.branch_id,
                    beat_id=beat_id,
                    shot_id=shot.shot_id,
                    prompt_hash=hash_prompt(shot.prompt),
                    status=AssetStatus.PENDING,
                    reuse_tags=shot.reuse_tags,
                ),
            )
            await self._append_event_by_scope(
                session_id=state.session_id,
                branch_id=state.branch_id,
                beat_id=beat_id,
                event_type=EventType.STORYBOARD_JOB_STARTED,
                payload={"job_id": job.job_id, "shot_id": shot.shot_id},
                links=EventLinks(asset_ids=[asset_id]),
            )
            logger.info(
                "storyboard job queued session_id=%s beat_id=%s shot_id=%s asset_id=%s",
                state.session_id,
                beat_id,
                shot.shot_id,
                asset_id,
            )
            self._track_task(asyncio.create_task(self._dispatch_asset_job(asset_id=asset_id, job=job)))

        should_render_video = state.video_budget_remaining > 0
        if should_render_video and shot_plan.hero_shots:
            hero = shot_plan.hero_shots[0]
            asset_id = stable_id("asset", f"{state.session_id}:{beat_id}:{hero.shot_id}:vid")
            existing = await self.repo.get_asset(state.session_id, asset_id)
            if existing is not None:
                return
            job = AssetJob(
                job_id=stable_id("job", asset_id),
                type=AssetType.HERO_VIDEO,
                priority="high",
                session_id=state.session_id,
                branch_id=state.branch_id,
                beat_id=beat_id,
                shot_id=hero.shot_id,
                prompt=hero.prompt,
                deadline_ms=18000,
            )
            await self.repo.upsert_asset(
                state.session_id,
                AssetRecord(
                    asset_id=asset_id,
                    type=AssetType.HERO_VIDEO,
                    session_id=state.session_id,
                    branch_id=state.branch_id,
                    beat_id=beat_id,
                    shot_id=hero.shot_id,
                    prompt_hash=hash_prompt(hero.prompt),
                    status=AssetStatus.PENDING,
                    reuse_tags=hero.reuse_tags,
                ),
            )
            state.video_budget_remaining -= 1
            await self.repo.upsert_session(state)
            await self._append_event_by_scope(
                session_id=state.session_id,
                branch_id=state.branch_id,
                beat_id=beat_id,
                event_type=EventType.VIDEO_JOB_STARTED,
                payload={"job_id": job.job_id, "shot_id": hero.shot_id},
                links=EventLinks(asset_ids=[asset_id]),
            )
            logger.info(
                "video job queued session_id=%s beat_id=%s shot_id=%s asset_id=%s",
                state.session_id,
                beat_id,
                hero.shot_id,
                asset_id,
            )
            self._track_task(asyncio.create_task(self._dispatch_asset_job(asset_id=asset_id, job=job)))

    async def _prepare_story_outline(self, *, state: SessionHotState, divergence_point: str) -> None:
        context = await self.repo.get_session_context(state.session_id)
        if context.get("story_outline_ready"):
            return

        planned_beat_ids: list[str] = []
        planned_shot_plans: dict[str, Any] = {}
        branch_rules: list[dict[str, Any]] = []
        active_entities: list[str] = []
        summary_lines: list[list[str]] = []
        previous_beat: BeatSpec | None = None

        for beat_index in range(1, state.target_beats + 1):
            beat_id = f"beat_{beat_index}"
            beat_spec = await self.agents.plan_beat(
                session_id=state.session_id,
                beat_id=beat_id,
                beat_index=beat_index,
                context={
                    "current_beat_objective": (
                        previous_beat.transition_hook
                        if previous_beat is not None
                        else f"Establish the first irreversible consequence of {divergence_point}"
                    ),
                    "active_entities": active_entities[:5],
                    "branch_rules": branch_rules[-5:],
                    "last_beat_summaries": summary_lines[-2:],
                    "previous_act_title": previous_beat.act_title if previous_beat is not None else "",
                    "previous_act_time_label": previous_beat.act_time_label if previous_beat is not None else "",
                    "previous_transition_hook": previous_beat.transition_hook if previous_beat is not None else "",
                    "rolling_story_summary": summary_lines[-4:],
                    "tone": state.user_tone,
                    "divergence_point": divergence_point,
                },
            )
            beat_spec = await self._sanitize_beat_spec(beat_spec)
            shot_plan = await self.agents.plan_shots(beat_spec)
            await self.repo.set_beat_spec(state.session_id, beat_id, beat_spec)
            planned_beat_ids.append(beat_id)
            planned_shot_plans[beat_id] = shot_plan.model_dump(mode="json")
            branch_rules.extend([update.model_dump(mode="json") for update in beat_spec.branch_rule_updates])
            active_entities = beat_spec.active_entities
            summary_lines.append(
                [
                    beat_spec.objective,
                    beat_spec.setup,
                    beat_spec.escalation,
                    beat_spec.consequence_seed,
                    beat_spec.transition_hook,
                ]
            )
            previous_beat = beat_spec

        await self.repo.set_session_context(
            state.session_id,
            {
                "branch_rules": branch_rules[-12:],
                "planned_beat_ids": planned_beat_ids,
                "planned_shot_plans": planned_shot_plans,
                "story_outline_ready": True,
            },
        )

    async def _sanitize_beat_spec(self, beat_spec: BeatSpec) -> BeatSpec:
        beat_spec.setup = await self.agents.safety_rewrite(beat_spec.setup)
        beat_spec.escalation = await self.agents.safety_rewrite(beat_spec.escalation)
        consistency = await self.agents.check_consistency(beat_spec)
        for fix in consistency.fixes:
            if fix.field == "setup":
                beat_spec.setup = fix.replacement
            if fix.field == "escalation":
                beat_spec.escalation = fix.replacement
        return beat_spec

    async def _load_or_plan_shot_plan(
        self,
        *,
        session_id: str,
        beat_id: str,
        beat_spec: BeatSpec,
    ) -> ShotPlan:
        context = await self.repo.get_session_context(session_id)
        planned_shot_plans = context.get("planned_shot_plans", {})
        raw_plan = planned_shot_plans.get(beat_id) if isinstance(planned_shot_plans, dict) else None
        if isinstance(raw_plan, dict):
            return ShotPlan.model_validate(raw_plan)

        shot_plan = await self.agents.plan_shots(beat_spec)
        planned_shot_plans = dict(planned_shot_plans) if isinstance(planned_shot_plans, dict) else {}
        planned_shot_plans[beat_id] = shot_plan.model_dump(mode="json")
        await self.repo.set_session_context(session_id, {"planned_shot_plans": planned_shot_plans})
        return shot_plan

    async def _prefetch_next_beat(self, state: SessionHotState) -> None:
        next_beat_index = state.beat_index + 1
        if next_beat_index > state.target_beats:
            return

        next_beat_id = f"beat_{next_beat_index}"
        beat_spec = await self.repo.get_beat_spec(state.session_id, next_beat_id)
        if beat_spec is None:
            return

        shot_plan = await self._load_or_plan_shot_plan(
            session_id=state.session_id,
            beat_id=next_beat_id,
            beat_spec=beat_spec,
        )
        interleaved = await self.repo.get_latest_interleaved_run(state.session_id, beat_id=next_beat_id)
        if interleaved is None:
            await self._generate_interleaved_for_beat(
                session_id=state.session_id,
                branch_id=state.branch_id,
                beat_id=next_beat_id,
                beat_index=next_beat_index,
                beat_spec=beat_spec,
                trigger=InterleavedTrigger.BEAT_START,
                question=None,
            )
        await self._dispatch_asset_jobs(state, beat_id=next_beat_id, shot_plan=shot_plan)

    async def _dispatch_asset_job(self, asset_id: str, job: AssetJob) -> None:
        try:
            await self.dispatcher.dispatch(
                asset_id=asset_id,
                job=job,
                callback_url=self.orchestrator_callback_url,
            )
            logger.info(
                "asset dispatch completed asset_id=%s session_id=%s beat_id=%s shot_id=%s type=%s",
                asset_id,
                job.session_id,
                job.beat_id,
                job.shot_id,
                job.type.value,
            )
        except Exception:
            logger.exception(
                "asset dispatch failed asset_id=%s session_id=%s beat_id=%s shot_id=%s type=%s",
                asset_id,
                job.session_id,
                job.beat_id,
                job.shot_id,
                job.type.value,
            )
            await self.asset_callback(
                AssetCallbackRequest(
                    asset_id=asset_id,
                    session_id=job.session_id,
                    beat_id=job.beat_id,
                    shot_id=job.shot_id,
                    status=AssetStatus.FAILED,
                    uri=None,
                    generation_time_ms=None,
                )
            )

    async def _generate_interleaved_for_beat(
        self,
        *,
        session_id: str,
        branch_id: str,
        beat_id: str,
        beat_index: int,
        beat_spec: BeatSpec,
        trigger: InterleavedTrigger,
        question: str | None,
    ) -> None:
        run_id = stable_id("ilv", f"{session_id}:{beat_id}:{trigger.value}:{utc_now_iso()}")
        logger.info(
            "interleaved generation started session_id=%s beat_id=%s trigger=%s run_id=%s",
            session_id,
            beat_id,
            trigger.value,
            run_id,
        )
        await self._append_event_by_scope(
            session_id=session_id,
            branch_id=branch_id,
            beat_id=beat_id,
            event_type=EventType.INTERLEAVED_GENERATION_STARTED,
            payload={
                "run_id": run_id,
                "trigger": trigger.value,
            },
        )

        generation: InterleavedGeneration | None = None
        for attempt in range(1, self.interleaved_max_attempts + 1):
            try:
                logger.info(
                    "interleaved attempt session_id=%s beat_id=%s trigger=%s attempt=%s",
                    session_id,
                    beat_id,
                    trigger.value,
                    attempt,
                )
                generation = await self.agents.generate_interleaved_story(
                    session_id=session_id,
                    beat_id=beat_id,
                    beat_index=beat_index,
                    beat_spec=beat_spec,
                    trigger=trigger,
                    question=question,
                )
                break
            except Exception as exc:
                retryable = self._is_retryable_interleaved_error(exc)
                should_retry = retryable and attempt < self.interleaved_max_attempts
                logger.warning(
                    "interleaved failed session_id=%s beat_id=%s trigger=%s attempt=%s retrying=%s error=%s",
                    session_id,
                    beat_id,
                    trigger.value,
                    attempt,
                    should_retry,
                    str(exc)[:200],
                )
                await self._append_event_by_scope(
                    session_id=session_id,
                    branch_id=branch_id,
                    beat_id=beat_id,
                    event_type=EventType.INTERLEAVED_FAILED,
                    payload={
                        "run_id": run_id,
                        "trigger": trigger.value,
                        "attempt": attempt,
                        "retrying": should_retry,
                        "error": str(exc)[:300],
                    },
                )
                if not should_retry:
                    return
                delay = self.interleaved_retry_base_delay_seconds * attempt
                await asyncio.sleep(delay)

        if generation is None:
            return
        logger.info(
            "interleaved ready session_id=%s beat_id=%s trigger=%s parts=%s model=%s request_id=%s",
            session_id,
            beat_id,
            trigger.value,
            len(generation.blocks),
            generation.model_id,
            generation.request_id,
        )

        sorted_blocks = sorted(generation.blocks, key=lambda block: block.part_order)
        run_record = InterleavedRunRecord(
            run_id=run_id,
            session_id=session_id,
            beat_id=beat_id,
            trigger=generation.trigger.value,
            model_id=generation.model_id,
            request_id=generation.request_id,
            created_ts=generation.ts.isoformat(),
            blocks=[
                InterleavedBlockRecord(
                    part_order=block.part_order,
                    kind=block.kind,
                    text=block.text,
                    mime_type=block.mime_type,
                    uri=self._proof_image_uri(run_id, block),
                    # Avoid oversized Firestore documents when image inline bytes are large.
                    inline_data_b64=None,
                )
                for block in sorted_blocks
            ],
        )
        try:
            await self.repo.upsert_interleaved_run(session_id, run_record)
        except Exception as exc:
            await self._append_event_by_scope(
                session_id=session_id,
                branch_id=branch_id,
                beat_id=beat_id,
                event_type=EventType.INTERLEAVED_FAILED,
                payload={
                    "run_id": run_id,
                    "trigger": trigger.value,
                    "attempt": self.interleaved_max_attempts,
                    "retrying": False,
                    "stage": "persist",
                    "error": str(exc)[:300],
                },
            )
            return

        self._interleaved_generations[(session_id, beat_id)] = InterleavedGeneration(
            run_id=run_record.run_id,
            session_id=session_id,
            beat_id=beat_id,
            trigger=generation.trigger,
            model_id=generation.model_id,
            request_id=generation.request_id,
            ts=generation.ts,
            blocks=sorted_blocks,
        )

        await self._append_event_by_scope(
            session_id=session_id,
            branch_id=branch_id,
            beat_id=beat_id,
            event_type=EventType.INTERLEAVED_READY,
            payload={
                "run_id": run_record.run_id,
                "trigger": run_record.trigger,
                "model_id": run_record.model_id,
                "request_id": run_record.request_id,
                "parts": len(run_record.blocks),
            },
        )

        for index, block in enumerate(sorted_blocks):
            await self._emit_action_for_session(
                session_id=session_id,
                action_type=UIActionType.SHOW_INTERLEAVED_BLOCKS,
                payload={
                    "run_id": run_record.run_id,
                    "beat_id": beat_id,
                    "trigger": run_record.trigger,
                    "model_id": run_record.model_id,
                    "request_id": run_record.request_id,
                    "append": True,
                    "final": index == len(sorted_blocks) - 1,
                    "blocks": [block.model_dump(mode="json")],
                },
            )

    @staticmethod
    def _is_retryable_interleaved_error(exc: Exception) -> bool:
        text = str(exc).lower()
        if "429" in text:
            return True
        if "resource exhausted" in text:
            return True
        if "503" in text or "502" in text or "500" in text:
            return True
        return False

    @staticmethod
    def _proof_image_uri(run_id: str, block: InterleavedBlockRecord | Any) -> str | None:
        if getattr(block, "kind", None) != "image":
            return getattr(block, "uri", None)
        uri = getattr(block, "uri", None)
        inline_data_b64 = getattr(block, "inline_data_b64", None)
        if uri:
            return uri
        if inline_data_b64:
            part_order = int(getattr(block, "part_order", 0))
            return f"inline://redacted/{run_id}/{part_order}"
        # Backward compatibility for previously persisted runs that dropped both fields.
        part_order = int(getattr(block, "part_order", 0))
        return f"inline://missing/{run_id}/{part_order}"

    async def _emit_action(
        self,
        state: SessionHotState,
        action_type: UIActionType,
        payload: dict[str, Any],
    ) -> None:
        action_seq = await self.repo.next_action_seq(state.session_id)
        action = UIAction(
            action_id=stable_id("act", f"{state.session_id}:{action_seq}:{action_type.value}"),
            type=action_type,
            payload=payload,
        )
        state.pending_actions.append(action.action_id)
        await self.repo.upsert_session(state)
        await self.action_bus.emit(state.session_id, action)
        await self._append_event(
            state=state,
            event_type=EventType.UI_ACTION_EMITTED,
            payload={"action_id": action.action_id, "type": action.type.value},
        )
        logger.debug(
            "ui action emitted session_id=%s action_id=%s type=%s",
            state.session_id,
            action.action_id,
            action.type.value,
        )

    async def _append_event(
        self,
        *,
        state: SessionHotState,
        event_type: EventType,
        payload: dict[str, Any],
        links: EventLinks | None = None,
    ) -> EventRecord:
        return await self._append_event_by_scope(
            session_id=state.session_id,
            branch_id=state.branch_id,
            beat_id=state.beat_id,
            event_type=event_type,
            payload=payload,
            links=links,
        )

    async def _emit_action_for_session(
        self,
        *,
        session_id: str,
        action_type: UIActionType,
        payload: dict[str, Any],
    ) -> None:
        state = await self._must_get_session(session_id)
        await self._emit_action(state, action_type, payload)

    async def _append_event_by_scope(
        self,
        *,
        session_id: str,
        branch_id: str,
        beat_id: str,
        event_type: EventType,
        payload: dict[str, Any],
        links: EventLinks | None = None,
    ) -> EventRecord:
        event = EventRecord(
            event_id=stable_id(
                "evt",
                f"{session_id}:{beat_id}:{event_type.value}:{utc_now_iso()}:{len(payload)}",
            ),
            session_id=session_id,
            branch_id=branch_id,
            beat_id=beat_id,
            type=event_type.value,
            payload=payload,
            links=links or EventLinks(),
        )
        await self.repo.append_event(event)
        return event

    async def _must_get_session(self, session_id: str) -> SessionHotState:
        try:
            return await self.repo.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from exc

    async def retry_pending_actions_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            session_ids = await self.action_bus.pending_sessions()
            for session_id in session_ids:
                await self.action_bus.retry_pending(session_id)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.5)
            except TimeoutError:
                continue

    def _track_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._log_task_result)

    @staticmethod
    def _log_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.error("background task failed", exc_info=(type(exc), exc, exc.__traceback__))

    @staticmethod
    def _compute_target_beats(divergence_point: str) -> int:
        text = divergence_point.lower()
        score = 0

        length = len(divergence_point.strip())
        if length >= 160:
            score += 3
        elif length >= 90:
            score += 2
        elif length >= 45:
            score += 1

        complexity_tokens = {
            "empire",
            "economy",
            "religion",
            "science",
            "technology",
            "climate",
            "colonial",
            "trade",
            "war",
            "governance",
            "democracy",
            "revolution",
        }
        token_hits = sum(1 for token in complexity_tokens if token in text)
        if token_hits >= 5:
            score += 3
        elif token_hits >= 3:
            score += 2
        elif token_hits >= 1:
            score += 1

        connector_hits = text.count(" and ") + text.count(" while ") + text.count(" then ")
        if connector_hits >= 3:
            score += 1

        return max(4, min(6, 4 + score))

    def _build_beat_summary(self, beat_spec: BeatSpec, choice_id: str) -> BeatSummary:
        lines = [
            beat_spec.objective,
            beat_spec.setup,
            beat_spec.escalation,
            f"Choice taken: {choice_id}",
            beat_spec.transition_hook,
        ]
        return BeatSummary(
            beat_id=beat_spec.beat_id,
            summary_5_lines=lines,
            new_facts=[rule.statement for rule in beat_spec.branch_rule_updates],
            new_entities=beat_spec.active_entities,
            causal_delta=f"Because {choice_id} was selected, branch incentives shifted.",
            open_threads=[beat_spec.transition_hook],
            asset_refs=[],
        )

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        normalized = value
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.utcnow()
