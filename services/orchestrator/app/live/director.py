from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, WebSocket, WebSocketDisconnect, status
from google.adk.agents import Agent
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import errors as genai_errors
from google.genai import types

from app.models.contracts import BeatSpec, DirectorSignalRequest
from app.models.enums import DirectorSignalType, Mode

if TYPE_CHECKING:
    from app.core.orchestrator import OrchestratorService

logger = logging.getLogger(__name__)

META_NARRATION_PREFIX = re.compile(
    r"^(?:considering|anchoring|crafting|narrating|visualizing|describing|focusing|initiating|delivering|refining|preparing|testing|analyzing|analysing|zeroing)\b",
    re.IGNORECASE,
)
META_NARRATION_PHRASE = re.compile(
    r"\b(?:i(?:'m| am)(?:\s+currently)?|i(?:'ll| have)?|my|objective|understanding|as instructed|provided script|focusing on|crafting the|describing the|integrating the|concentrating on|immersed in|executing the|setting the scene|building up|critical requirement|requested wording|verbatim|strict adherence|precise replication|deliver the given)\b",
    re.IGNORECASE,
)
CONTROL_TOKEN_PATTERN = re.compile(r"<[^>]+>")
ONBOARDING_WELCOME_PREFIX = "welcome to whatif"
ONBOARDING_WELCOME_SCRIPT = (
    "Welcome to WhatIf. Say the one moment that breaks history, and I will show you the world that follows."
)


VOICE_DIRECTOR_INSTRUCTION = """
You are the WhatIf Voice Director, a real-time cinematic narrator.

Rules:
1. Keep narration immersive, detailed, and scene-first.
2. Never output raw JSON in spoken responses.
3. Only describe the currently grounded act, visuals, and consequences supplied by the app.
4. Never invent onboarding progress, tool usage, hidden steps, or future acts that were not provided.
5. When a scene packet references images or a video, explicitly describe what the viewer is seeing before explaining why it matters.
6. If grounding is thin, stay narrow and say less rather than hallucinating.
6a. Never reveal planning, drafting, reasoning, testing notes, or internal process.
6b. Never say phrases like "I'm working on", "I'm refining", "I'm testing", headings, markdown, or stage directions.
6c. When the app sends a single current-scene prompt or on-screen story text, narrate only that one visible scene and stop cleanly.
3. When user intent matches control actions, use tools instead of narration control text:
   - signal_continue_act when user confirms continuing to next act
   - signal_interrupt for WHY/PAUSE/REWIND/COMPARE/CHANGE_TONE
   - signal_ready_for_choices when user asks to pick now
   - signal_pace_hint when user asks faster/slower pacing
   - signal_emotion_target for emotional direction changes
7. During onboarding, stay brief. After the user states the divergence point, stay silent and let the story engine take over.
8. After signal_continue_act succeeds, immediately narrate the new act without waiting for another prompt.
9. When the user asks a question mid-scene, answer it directly and then guide them back into the current moment.
10. For normal story progression, narrate with strong cinematic pacing
   and keep momentum unless the user is clearly still speaking.
""".strip()


@dataclass(slots=True)
class LiveDirectorSettings:
    app_name: str
    model: str
    voice_name: str
    input_audio_mime_type: str
    enable_affective_dialog: bool
    enable_proactive_audio: bool
    max_retries: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float


@dataclass(slots=True)
class PendingLiveTextTurn:
    text: str


class VoiceDirectorTools:
    """Tool surface that lets the live agent emit typed director signals."""

    def __init__(self, orchestrator: OrchestratorService) -> None:
        self.orchestrator = orchestrator

    async def signal_interrupt(
        self,
        kind: str,
        question: str | None = None,
        tool_context: Any | None = None,
    ) -> dict[str, Any]:
        """Emit interruption intent for WHY/PAUSE/REWIND/COMPARE/CHANGE_TONE."""
        session_id = self._session_id_from_context(tool_context)
        if session_id is None:
            return {"ok": False, "error": "missing session context"}

        payload: dict[str, Any] = {"kind": kind.upper()}
        if question:
            payload["question"] = question

        await self.orchestrator.handle_director_signal(
            session_id,
            DirectorSignalRequest(type=DirectorSignalType.INTERRUPT, payload=payload),
        )
        return {"ok": True}

    async def signal_continue_act(self, tool_context: Any | None = None) -> dict[str, Any]:
        """Continue from intermission after user confirms."""
        session_id = self._session_id_from_context(tool_context)
        if session_id is None:
            return {"ok": False, "error": "missing session context"}

        state = await self.orchestrator.repo.get_session(session_id)
        context = await self.orchestrator.repo.get_session_context(session_id)
        snapshot = context.get("scene_snapshot", {})
        summary_active = False
        if isinstance(snapshot, dict):
            phase = str(snapshot.get("phase", "")).strip().lower()
            story_mode = snapshot.get("story_mode", {})
            summary_active = phase == "actsummary" or (
                isinstance(story_mode, dict) and bool(story_mode.get("summary"))
            )

        ready_to_continue = state.mode in {Mode.INTERMISSION, Mode.CHOICE} or (
            state.mode == Mode.STORY and summary_active
        )
        if not ready_to_continue:
            return {"ok": False, "error": "session not ready to continue"}

        try:
            await self.orchestrator.continue_session(session_id)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                return {"ok": False, "error": "session not ready to continue"}
            raise
        return {"ok": True}

    async def signal_ready_for_choices(self, tool_context: Any | None = None) -> dict[str, Any]:
        """Emit READY_FOR_CHOICES when narration reaches a branch point."""
        session_id = self._session_id_from_context(tool_context)
        if session_id is None:
            return {"ok": False, "error": "missing session context"}

        await self.orchestrator.handle_director_signal(
            session_id,
            DirectorSignalRequest(type=DirectorSignalType.READY_FOR_CHOICES, payload={}),
        )
        return {"ok": True}

    async def signal_pace_hint(
        self,
        pace: str,
        tool_context: Any | None = None,
    ) -> dict[str, Any]:
        """Emit pace hint. Valid values include fast, normal, slow."""
        session_id = self._session_id_from_context(tool_context)
        if session_id is None:
            return {"ok": False, "error": "missing session context"}

        await self.orchestrator.handle_director_signal(
            session_id,
            DirectorSignalRequest(type=DirectorSignalType.PACE_HINT, payload={"pace": pace}),
        )
        return {"ok": True}

    async def signal_emotion_target(
        self,
        target: str,
        tool_context: Any | None = None,
    ) -> dict[str, Any]:
        """Emit emotion target for delivery (for example: tense, hopeful, solemn)."""
        session_id = self._session_id_from_context(tool_context)
        if session_id is None:
            return {"ok": False, "error": "missing session context"}

        await self.orchestrator.handle_director_signal(
            session_id,
            DirectorSignalRequest(type=DirectorSignalType.EMOTION_TARGET, payload={"target": target}),
        )
        return {"ok": True}

    @staticmethod
    def _session_id_from_context(tool_context: Any | None) -> str | None:
        if tool_context is None:
            return None
        session = getattr(tool_context, "session", None)
        if session is None:
            return None
        session_id = getattr(session, "id", None)
        if session_id is None:
            return None
        return str(session_id)


class LiveDirectorService:
    def __init__(
        self,
        *,
        orchestrator: OrchestratorService,
        settings: LiveDirectorSettings,
    ) -> None:
        self.orchestrator = orchestrator
        self.settings = settings
        self.session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
        self.tools = VoiceDirectorTools(orchestrator)
        self._scene_snapshots: dict[str, dict[str, Any]] = {}
        self._pending_text_turns: dict[str, PendingLiveTextTurn] = {}

        self.root_agent = Agent(
            name="whatif_live_director",
            model=settings.model,
            description="Real-time voice director for WhatIf cinematic sessions",
            instruction=VOICE_DIRECTOR_INSTRUCTION,
            generate_content_config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
            tools=[
                self.tools.signal_continue_act,
                self.tools.signal_interrupt,
                self.tools.signal_ready_for_choices,
                self.tools.signal_pace_hint,
                self.tools.signal_emotion_target,
            ],
        )

        self.runner = Runner(
            app_name=settings.app_name,
            agent=self.root_agent,
            session_service=self.session_service,
            auto_create_session=False,
        )

    async def handle_websocket(self, websocket: WebSocket, *, session_id: str, user_id: str) -> None:
        await websocket.accept()
        logger.info("live websocket connected session_id=%s user_id=%s", session_id, user_id)

        await self._ensure_adk_session(user_id=user_id, session_id=session_id)

        live_request_queue = LiveRequestQueue()  # type: ignore[no-untyped-call]
        await self._seed_scene_context(session_id=session_id, live_request_queue=live_request_queue)

        run_config = self._build_run_config()

        producer = asyncio.create_task(
            self._producer(
                websocket=websocket,
                user_id=user_id,
                session_id=session_id,
                live_request_queue=live_request_queue,
                run_config=run_config,
            )
        )
        consumer = asyncio.create_task(
            self._consumer(websocket=websocket, session_id=session_id, live_request_queue=live_request_queue)
        )

        done, pending = await asyncio.wait(
            {producer, consumer},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            exc = task.exception()
            if exc is None:
                continue
            if isinstance(exc, WebSocketDisconnect):
                logger.info("live websocket disconnected session_id=%s user_id=%s", session_id, user_id)
                continue
            logger.error(
                "live websocket failed session_id=%s user_id=%s",
                session_id,
                user_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            await self._emit_live_status(
                websocket,
                status="failed",
                message="Live narration is temporarily unavailable. Reconnecting should recover it.",
            )
            await self._close_websocket(websocket)
            live_request_queue.close()  # type: ignore[no-untyped-call]
            return

        live_request_queue.close()  # type: ignore[no-untyped-call]
        logger.info("live websocket closed session_id=%s user_id=%s", session_id, user_id)

    async def _producer(
        self,
        *,
        websocket: WebSocket,
        user_id: str,
        session_id: str,
        live_request_queue: LiveRequestQueue,
        run_config: RunConfig,
    ) -> None:
        attempt = 0
        while True:
            saw_model_output = False
            try:
                async for event in self.runner.run_live(
                    user_id=user_id,
                    session_id=session_id,
                    live_request_queue=live_request_queue,
                    run_config=run_config,
                ):
                    attempt = 0
                    if self._event_has_model_output(event):
                        saw_model_output = True
                        self._pending_text_turns.pop(session_id, None)

                    payload = event.model_dump(mode="json", exclude_none=True)
                    await websocket.send_json({"type": "adk_event", "event": payload})
                    logger.debug("live adk event session_id=%s keys=%s", session_id, sorted(payload.keys()))

                    if event.input_transcription and event.input_transcription.text:
                        logger.info(
                            "live input transcript session_id=%s final=%s text=%s",
                            session_id,
                            bool(event.input_transcription.finished),
                            event.input_transcription.text[:200],
                        )
                        await self._emit_transcript(
                            websocket,
                            transcript_type="input_transcript",
                            text=event.input_transcription.text,
                            final=bool(event.input_transcription.finished),
                        )

                    emitted_output_transcript = False
                    if event.output_transcription and event.output_transcription.text:
                        output_text = self._sanitize_spoken_output(session_id, event.output_transcription.text)
                        if output_text:
                            emitted_output_transcript = True
                            logger.info(
                                "live output transcript session_id=%s final=%s text=%s",
                                session_id,
                                bool(event.output_transcription.finished),
                                output_text[:200],
                            )
                            await self._emit_transcript(
                                websocket,
                                transcript_type="output_transcript",
                                text=output_text,
                                final=bool(event.output_transcription.finished),
                            )
                            if event.output_transcription.finished:
                                await self.orchestrator.caption_bus.emit(session_id, output_text)

                    if event.content is None or not event.content.parts:
                        continue

                    fallback_text_parts: list[str] = []
                    for part in event.content.parts:
                        part_text = getattr(part, "text", None)
                        if isinstance(part_text, str):
                            stripped = part_text.strip()
                            if stripped:
                                fallback_text_parts.append(stripped)
                        if not part.inline_data:
                            continue
                        mime_type = part.inline_data.mime_type or ""
                        if not mime_type.startswith("audio/"):
                            continue

                        inline_data = part.inline_data.data
                        if inline_data is None:
                            continue
                        await self._emit_audio_chunk(
                            websocket,
                            session_id=session_id,
                            mime_type=mime_type,
                            inline_data=inline_data,
                        )

                    if fallback_text_parts and not emitted_output_transcript:
                        fallback_text = self._sanitize_spoken_output(session_id, " ".join(fallback_text_parts))
                        if not fallback_text:
                            continue
                        self._pending_text_turns.pop(session_id, None)
                        logger.info("live fallback text session_id=%s text=%s", session_id, fallback_text[:200])
                        await self._emit_transcript(
                            websocket,
                            transcript_type="output_transcript",
                            text=fallback_text,
                            final=True,
                        )
                        await self.orchestrator.caption_bus.emit(session_id, fallback_text)
                return
            except Exception as exc:
                retry_delay = self._retry_delay_seconds(attempt, exc)
                if retry_delay is None:
                    raise

                attempt += 1
                replayed_turn = False
                if not saw_model_output:
                    replayed_turn = await self._replay_pending_text_turn(
                        session_id=session_id,
                        live_request_queue=live_request_queue,
                    )

                logger.warning(
                    "live session retry session_id=%s attempt=%s delay=%.2fs replayed_turn=%s error=%s",
                    session_id,
                    attempt,
                    retry_delay,
                    replayed_turn,
                    exc,
                )
                await self._emit_live_status(
                    websocket,
                    status="reconnecting",
                    message="Live narration connection dropped. Retrying.",
                    attempt=attempt,
                    retry_in_ms=int(retry_delay * 1000),
                )
                await asyncio.sleep(retry_delay)

    async def _consumer(
        self,
        *,
        websocket: WebSocket,
        session_id: str,
        live_request_queue: LiveRequestQueue,
    ) -> None:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect

            audio_bytes = message.get("bytes")
            if isinstance(audio_bytes, (bytes, bytearray)):
                logger.debug("live audio input chunk session_id=%s bytes=%s", session_id, len(audio_bytes))
                live_request_queue.send_realtime(
                    types.Blob(data=bytes(audio_bytes), mime_type=self.settings.input_audio_mime_type)
                )
                continue

            text_payload = message.get("text")
            if text_payload is None:
                continue

            await self._handle_client_text_message(
                text_payload=text_payload,
                session_id=session_id,
                live_request_queue=live_request_queue,
            )

    async def _handle_client_text_message(
        self,
        *,
        text_payload: str,
        session_id: str,
        live_request_queue: LiveRequestQueue,
    ) -> None:
        parsed = self._parse_client_payload(text_payload)
        message_type = parsed.get("type", "text")

        if message_type == "scene_snapshot":
            await self._handle_scene_snapshot(session_id=session_id, snapshot=parsed.get("snapshot"))
            return

        if message_type in {"activity_start", "activity_end"}:
            await self._handle_activity_marker(
                session_id=session_id,
                message_type=message_type,
                live_request_queue=live_request_queue,
            )
            return

        if message_type == "audio":
            self._handle_audio_payload(parsed=parsed, live_request_queue=live_request_queue)
            return

        await self._handle_text_payload(
            session_id=session_id,
            parsed=parsed,
            live_request_queue=live_request_queue,
        )

    async def _ensure_adk_session(self, *, user_id: str, session_id: str) -> None:
        existing = await self.session_service.get_session(
            app_name=self.settings.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if existing is not None:
            return

        await self.session_service.create_session(
            app_name=self.settings.app_name,
            user_id=user_id,
            session_id=session_id,
            state={"product": "whatif", "voice": "director"},
        )

    async def _seed_scene_context(self, *, session_id: str, live_request_queue: LiveRequestQueue) -> None:
        _ = live_request_queue
        try:
            context = await self.orchestrator.repo.get_session_context(session_id)
        except KeyError:
            logger.warning("live seed skipped missing session session_id=%s", session_id)
            return

        snapshot = context.get("scene_snapshot")
        if isinstance(snapshot, dict):
            self._scene_snapshots[session_id] = self._bounded_snapshot(snapshot)
        logger.info("live seed primed session_id=%s snapshot_keys=%s", session_id, sorted(self._scene_snapshots.get(session_id, {}).keys()))

    async def _inject_scene_snapshot_context(
        self,
        *,
        session_id: str,
        live_request_queue: LiveRequestQueue,
    ) -> None:
        snapshot = self._scene_snapshots.get(session_id)
        if not snapshot:
            return

        summary = self._snapshot_summary(snapshot)
        if not summary:
            return

        live_request_queue.send_content(
            types.Content(
                role="user",
                parts=[types.Part(text=summary)],
            )
        )

    async def _emit_live_status(
        self,
        websocket: WebSocket,
        *,
        status: str,
        message: str | None = None,
        attempt: int | None = None,
        retry_in_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"type": "live_status", "status": status}
        if message:
            payload["message"] = message
        if attempt is not None:
            payload["attempt"] = attempt
        if retry_in_ms is not None:
            payload["retry_in_ms"] = retry_in_ms
        await self._safe_send_json(websocket, payload)

    async def _emit_transcript(
        self,
        websocket: WebSocket,
        *,
        transcript_type: str,
        text: str,
        final: bool,
    ) -> None:
        await websocket.send_json({"type": transcript_type, "text": text, "final": final})

    async def _safe_send_json(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:
            logger.debug("live websocket send skipped payload_type=%s", payload.get("type"))

    async def _close_websocket(self, websocket: WebSocket) -> None:
        try:
            await websocket.close(code=1013)
        except Exception:
            logger.debug("live websocket close skipped")

    async def _emit_audio_chunk(
        self,
        websocket: WebSocket,
        *,
        session_id: str,
        mime_type: str,
        inline_data: bytes | str,
    ) -> None:
        audio_b64 = self._audio_base64(inline_data)
        await websocket.send_json({"type": "audio_chunk", "mime_type": mime_type, "data": audio_b64})
        logger.debug(
            "live audio chunk session_id=%s mime_type=%s bytes=%s",
            session_id,
            mime_type,
            len(audio_b64),
        )

    async def _handle_scene_snapshot(self, *, session_id: str, snapshot: Any) -> None:
        if not isinstance(snapshot, dict):
            return
        bounded = self._bounded_snapshot(snapshot)
        self._scene_snapshots[session_id] = bounded
        await self.orchestrator.update_scene_snapshot(session_id, bounded)
        logger.debug("live scene snapshot updated session_id=%s keys=%s", session_id, sorted(bounded.keys()))

    async def _handle_activity_marker(
        self,
        *,
        session_id: str,
        message_type: str,
        live_request_queue: LiveRequestQueue,
    ) -> None:
        logger.info("live %s session_id=%s", message_type, session_id)
        if message_type == "activity_start":
            live_request_queue.send_activity_start()  # type: ignore[no-untyped-call]
            await self._inject_scene_snapshot_context(session_id=session_id, live_request_queue=live_request_queue)
            return
        live_request_queue.send_activity_end()  # type: ignore[no-untyped-call]

    def _handle_audio_payload(self, *, parsed: dict[str, Any], live_request_queue: LiveRequestQueue) -> None:
        raw = parsed.get("data")
        if not isinstance(raw, str):
            return
        mime_type = parsed.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = self.settings.input_audio_mime_type
        chunk = base64.b64decode(raw)
        live_request_queue.send_realtime(types.Blob(data=chunk, mime_type=mime_type))

    async def _handle_text_payload(
        self,
        *,
        session_id: str,
        parsed: dict[str, Any],
        live_request_queue: LiveRequestQueue,
    ) -> None:
        text = parsed.get("text")
        if not isinstance(text, str) or not text.strip():
            return

        cleaned = text.strip()
        logger.info("live text input session_id=%s text=%s", session_id, cleaned[:200])
        self._pending_text_turns[session_id] = PendingLiveTextTurn(text=cleaned)
        await self._inject_scene_snapshot_context(session_id=session_id, live_request_queue=live_request_queue)
        live_request_queue.send_content(
            types.Content(
                role="user",
                parts=[types.Part(text=cleaned)],
            )
        )

    async def _replay_pending_text_turn(
        self,
        *,
        session_id: str,
        live_request_queue: LiveRequestQueue,
    ) -> bool:
        pending_turn = self._pending_text_turns.get(session_id)
        if pending_turn is None:
            return False

        await self._inject_scene_snapshot_context(session_id=session_id, live_request_queue=live_request_queue)
        live_request_queue.send_content(
            types.Content(
                role="user",
                parts=[types.Part(text=pending_turn.text)],
            )
        )
        logger.info("replayed live text turn session_id=%s text=%s", session_id, pending_turn.text[:200])
        return True

    @staticmethod
    def _event_has_model_output(event: Any) -> bool:
        output_transcription = getattr(event, "output_transcription", None)
        if output_transcription and getattr(output_transcription, "text", None):
            return True

        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None)
        if not parts:
            return False

        for part in parts:
            if getattr(part, "inline_data", None):
                return True
        return False

    def _retry_delay_seconds(self, attempt: int, exc: Exception) -> float | None:
        if attempt >= self.settings.max_retries:
            return None
        if not self._is_retryable_live_error(exc):
            return None
        return min(
            self.settings.retry_max_delay_seconds,
            self.settings.retry_base_delay_seconds * (2**attempt),
        )

    @classmethod
    def _is_retryable_live_error(cls, exc: Exception) -> bool:
        retryable_codes = {1006, 1011, 1012, 1013}
        for current in cls._exception_chain(exc):
            if isinstance(current, asyncio.TimeoutError):
                return True
            code = getattr(current, "code", None)
            if code in retryable_codes:
                return True
            if isinstance(current, genai_errors.APIError):
                if current.code in retryable_codes:
                    return True
                status_code = getattr(current, "status_code", None)
                if status_code in retryable_codes:
                    return True
        return False

    @staticmethod
    def _exception_chain(exc: Exception) -> list[BaseException]:
        chain: list[BaseException] = []
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            chain.append(current)
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        return chain

    def _build_run_config(self) -> RunConfig:
        return RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.settings.voice_name)
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
            ),
            enable_affective_dialog=self.settings.enable_affective_dialog,
            proactivity=types.ProactivityConfig(proactive_audio=self.settings.enable_proactive_audio),
        )

    @staticmethod
    def _parse_client_payload(payload: str) -> dict[str, Any]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {"type": "text", "text": payload}
        if isinstance(parsed, dict):
            return parsed
        return {"type": "text", "text": payload}

    @staticmethod
    def _bounded_snapshot(snapshot: dict[str, Any], max_chars: int = 3000) -> dict[str, Any]:
        serialized = json.dumps(snapshot, ensure_ascii=True)
        if len(serialized) <= max_chars:
            return snapshot
        return {"truncated": True, "snippet": serialized[:max_chars]}

    @staticmethod
    def _snapshot_summary(snapshot: dict[str, Any]) -> str:
        try:
            summary = json.dumps(snapshot, ensure_ascii=True)
        except Exception:
            return ""
        if not summary:
            return ""
        return f"Current on-screen scene snapshot (for grounding): {summary[:2500]}"

    @staticmethod
    def _audio_base64(value: bytes | str) -> str:
        if isinstance(value, str):
            return value
        return base64.b64encode(value).decode("utf-8")

    def _sanitize_spoken_output(self, session_id: str, text: str) -> str | None:
        normalized = CONTROL_TOKEN_PATTERN.sub(" ", text)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None

        phase = str(self._scene_snapshots.get(session_id, {}).get("phase", "")).strip().lower()
        if phase not in {"acting", "actreveal", "onboarding"}:
            return normalized

        without_markdown_label = re.sub(r"^(?:\*\*|__|`)([^*_`]{1,80}?)(?:\*\*|__|`)\s+", "", normalized).strip()
        if not without_markdown_label:
            return None

        if phase == "onboarding":
            lowered = without_markdown_label.lower()
            welcome_index = lowered.find(ONBOARDING_WELCOME_PREFIX)
            if welcome_index >= 0:
                candidate = without_markdown_label[welcome_index:].strip()
                if ONBOARDING_WELCOME_SCRIPT.lower() in candidate.lower():
                    return ONBOARDING_WELCOME_SCRIPT
                return candidate
            if META_NARRATION_PREFIX.match(without_markdown_label) or META_NARRATION_PHRASE.search(without_markdown_label):
                logger.warning(
                    "filtered live onboarding meta transcript session_id=%s text=%s",
                    session_id,
                    normalized[:200],
                )
                return None
            return without_markdown_label

        if phase in {"acting", "actreveal"} and (
            META_NARRATION_PREFIX.match(without_markdown_label) or META_NARRATION_PHRASE.search(without_markdown_label)
        ):
            logger.warning(
                "filtered live meta transcript session_id=%s phase=%s text=%s",
                session_id,
                phase,
                normalized[:200],
            )
            return None
        return without_markdown_label

    @staticmethod
    def _scene_seed_prompt(*, beat_index: int, beat_spec: BeatSpec) -> str:
        return (
            f"Act {beat_index} context. Title: {beat_spec.act_title}. Time: {beat_spec.act_time_label}.\n"
            f"Objective: {beat_spec.objective}\n"
            f"Setup: {beat_spec.setup}\n"
            f"Escalation: {beat_spec.escalation}\n"
            "Narrate only this act. Ground the delivery in visible imagery and explain the consequences in detail. "
            "Do not mention branch choices or backstage process."
        )


class StubLiveDirectorService:
    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, Any]] = {}

    async def handle_websocket(self, websocket: WebSocket, *, session_id: str, user_id: str) -> None:
        _ = user_id
        await websocket.accept()
        logger.info("stub live websocket connected session_id=%s", session_id)
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect

                text_payload = message.get("text")
                if text_payload is None:
                    continue
                parsed = LiveDirectorService._parse_client_payload(text_payload)
                message_type = parsed.get("type", "text")
                if message_type == "scene_snapshot":
                    snapshot = parsed.get("snapshot")
                    if isinstance(snapshot, dict):
                        self._snapshots[session_id] = snapshot
                    continue

                if message_type != "text":
                    continue

                prompt = str(parsed.get("text", "")).strip()
                if not prompt:
                    continue
                narration = self._build_stub_narration(prompt, self._snapshots.get(session_id, {}))
                await websocket.send_json({"type": "output_transcript", "text": narration, "final": True})
        except WebSocketDisconnect:
            logger.info("stub live websocket disconnected session_id=%s", session_id)

    @staticmethod
    def _build_stub_narration(prompt: str, snapshot: dict[str, Any]) -> str:
        if "Act title:" not in prompt:
            return "Describe the divergence point, and I will turn it into a full alternate-history chronicle."

        title = _extract_after(prompt, "Act title:")
        time_label = _extract_after(prompt, "Time label:")
        opening = _extract_after(prompt, "Opening movement:")
        escalation = _extract_after(prompt, "Escalation:")
        visuals = _extract_after(prompt, "Visual sequence:")
        video_note = _extract_after(prompt, "Video status:")
        moment_caption = str(snapshot.get("moment_caption", "")).strip()

        parts = [
            f"{title or 'This act'} begins in {time_label or 'an altered era'}, and the frame opens on {moment_caption or opening or 'the first decisive consequence of the divergence'}.",
            opening or "The altered timeline settles into view through political tension, spectacle, and hard consequence.",
            escalation or "Pressure builds as the world reacts to the consequences of the first change.",
        ]
        if visuals:
            parts.append(f"On screen, the sequence moves through {visuals}.")
        if video_note:
            parts.append(video_note)
        return " ".join(part.strip() for part in parts if part.strip())


def create_live_director_from_env(*, orchestrator: OrchestratorService) -> LiveDirectorService | StubLiveDirectorService:
    if _env_bool("WHATIF_LOCAL_STUBS", False):
        logger.info("using stub live director")
        return StubLiveDirectorService()

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY must be set for live Gemini runtime")

    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
    os.environ.setdefault("GOOGLE_API_KEY", api_key)

    settings = LiveDirectorSettings(
        app_name=os.getenv("WHATIF_LIVE_APP_NAME", "whatif-live"),
        model=_normalize_live_model(os.getenv("WHATIF_LIVE_MODEL")),
        voice_name=os.getenv("WHATIF_LIVE_VOICE", "Aoede"),
        input_audio_mime_type=os.getenv("WHATIF_INPUT_AUDIO_MIME", "audio/pcm;rate=16000"),
        enable_affective_dialog=_env_bool("WHATIF_ENABLE_AFFECTIVE_DIALOG", True),
        enable_proactive_audio=_env_bool("WHATIF_ENABLE_PROACTIVE_AUDIO", False),
        max_retries=_env_int("WHATIF_LIVE_MAX_RETRIES", 3),
        retry_base_delay_seconds=_env_float("WHATIF_LIVE_RETRY_BASE_DELAY_SECONDS", 0.75),
        retry_max_delay_seconds=_env_float("WHATIF_LIVE_RETRY_MAX_DELAY_SECONDS", 4.0),
    )
    logger.info(
        "live director configured model=%s voice=%s input_audio=%s affective=%s proactive=%s retries=%s base_delay=%.2fs max_delay=%.2fs",
        settings.model,
        settings.voice_name,
        settings.input_audio_mime_type,
        settings.enable_affective_dialog,
        settings.enable_proactive_audio,
        settings.max_retries,
        settings.retry_base_delay_seconds,
        settings.retry_max_delay_seconds,
    )
    return LiveDirectorService(orchestrator=orchestrator, settings=settings)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("invalid integer env %s=%r; using default=%s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        logger.warning("invalid float env %s=%r; using default=%s", name, raw, default)
        return default


def _normalize_live_model(model: str | None) -> str:
    normalized = (model or "").strip()
    if not normalized:
        return "gemini-2.5-flash-native-audio-preview-12-2025"
    if normalized in {
        "gemini-2.5-flash-native-audio-latest",
        "gemini-live-2.5-flash-native-audio",
        "gemini-live-2.5-flash-preview-native-audio-dialog",
        "gemini-live-2.5-flash-preview",
    }:
        return "gemini-2.5-flash-native-audio-preview-12-2025"
    return normalized


def _extract_after(text: str, marker: str) -> str:
    if marker not in text:
        return ""
    segment = text.split(marker, 1)[1]
    for stop_marker in [
        "Act title:",
        "Time label:",
        "Opening movement:",
        "Escalation:",
        "Narration guidance:",
        "Visual sequence:",
        "Video status:",
    ]:
        if stop_marker == marker:
            continue
        if stop_marker in segment:
            segment = segment.split(stop_marker, 1)[0]
    return segment.strip().strip(".")
