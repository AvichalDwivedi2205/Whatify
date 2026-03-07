from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents import Agent
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.models.contracts import BeatSpec, BeginSessionRequest, DirectorSignalRequest
from app.models.enums import DirectorSignalType

if TYPE_CHECKING:
    from app.core.orchestrator import OrchestratorService

logger = logging.getLogger(__name__)


VOICE_DIRECTOR_INSTRUCTION = """
You are the WhatIf Voice Director, a real-time cinematic narrator.

Rules:
1. Keep narration immersive, concise, and scene-first.
2. Never output raw JSON in spoken responses.
3. When user intent matches control actions, use tools instead of narration control text:
   - signal_continue_act when user confirms continuing to next act
   - signal_interrupt for WHY/PAUSE/REWIND/COMPARE/CHANGE_TONE
   - signal_ready_for_choices when user asks to pick now
   - signal_pace_hint when user asks faster/slower pacing
   - signal_emotion_target for emotional direction changes
4. During onboarding, ask for the divergence point once. After the user states it, stay silent and let the main story engine take over.
5. After signal_continue_act succeeds, immediately narrate the new act without waiting for another prompt.
6. When the user asks a question mid-scene, answer it directly and then guide them back into the current moment.
7. For normal story progression, narrate with strong cinematic pacing and keep momentum unless the user is clearly still speaking.
""".strip()


@dataclass(slots=True)
class LiveDirectorSettings:
    app_name: str
    model: str
    voice_name: str
    input_audio_mime_type: str
    enable_affective_dialog: bool
    enable_proactive_audio: bool


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

    async def signal_story_brief_captured(
        self,
        divergence_point: str,
        tone: str | None = None,
        pacing: str | None = None,
        tool_context: Any | None = None,
    ) -> dict[str, Any]:
        """Start story generation from captured onboarding divergence prompt."""
        session_id = self._session_id_from_context(tool_context)
        if session_id is None:
            return {"ok": False, "error": "missing session context"}

        cleaned = divergence_point.strip()
        if not cleaned:
            return {"ok": False, "error": "empty divergence_point"}

        await self.orchestrator.begin_session(
            session_id,
            BeginSessionRequest(
                divergence_point=cleaned,
                tone=(tone or "cinematic").strip() or "cinematic",
                pacing=(pacing or "normal").strip() or "normal",
            ),
        )
        return {"ok": True}

    async def signal_continue_act(self, tool_context: Any | None = None) -> dict[str, Any]:
        """Continue from intermission after user confirms."""
        session_id = self._session_id_from_context(tool_context)
        if session_id is None:
            return {"ok": False, "error": "missing session context"}

        await self.orchestrator.continue_session(session_id)
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

        self.root_agent = Agent(
            name="whatif_live_director",
            model=settings.model,
            description="Real-time voice director for WhatIf cinematic sessions",
            instruction=VOICE_DIRECTOR_INSTRUCTION,
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
            raise exc

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
        async for event in self.runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
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
                await websocket.send_json(
                    {
                        "type": "input_transcript",
                        "text": event.input_transcription.text,
                        "final": bool(event.input_transcription.finished),
                    }
                )

            emitted_output_transcript = False
            if event.output_transcription and event.output_transcription.text:
                output_text = event.output_transcription.text.strip()
                if output_text:
                    emitted_output_transcript = True
                    logger.info(
                        "live output transcript session_id=%s final=%s text=%s",
                        session_id,
                        bool(event.output_transcription.finished),
                        output_text[:200],
                    )
                    await websocket.send_json(
                        {
                            "type": "output_transcript",
                            "text": output_text,
                            "final": bool(event.output_transcription.finished),
                        }
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
                audio_b64 = self._audio_base64(inline_data)
                await websocket.send_json(
                    {
                        "type": "audio_chunk",
                        "mime_type": mime_type,
                        "data": audio_b64,
                    }
                )
                logger.debug(
                    "live audio chunk session_id=%s mime_type=%s bytes=%s",
                    session_id,
                    mime_type,
                    len(audio_b64),
                )

            if fallback_text_parts and not emitted_output_transcript:
                fallback_text = " ".join(fallback_text_parts)
                logger.info("live fallback text session_id=%s text=%s", session_id, fallback_text[:200])
                await websocket.send_json(
                    {
                        "type": "output_transcript",
                        "text": fallback_text,
                        "final": True,
                    }
                )
                await self.orchestrator.caption_bus.emit(session_id, fallback_text)

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
            snapshot = parsed.get("snapshot")
            if not isinstance(snapshot, dict):
                return
            bounded = self._bounded_snapshot(snapshot)
            self._scene_snapshots[session_id] = bounded
            await self.orchestrator.update_scene_snapshot(session_id, bounded)
            logger.debug("live scene snapshot updated session_id=%s keys=%s", session_id, sorted(bounded.keys()))
            return

        if message_type == "activity_start":
            logger.info("live activity_start session_id=%s", session_id)
            live_request_queue.send_activity_start()  # type: ignore[no-untyped-call]
            await self._inject_scene_snapshot_context(session_id=session_id, live_request_queue=live_request_queue)
            return

        if message_type == "activity_end":
            logger.info("live activity_end session_id=%s", session_id)
            live_request_queue.send_activity_end()  # type: ignore[no-untyped-call]
            return

        if message_type == "audio":
            raw = parsed.get("data")
            if not isinstance(raw, str):
                return
            mime_type = parsed.get("mime_type")
            if not isinstance(mime_type, str) or not mime_type:
                mime_type = self.settings.input_audio_mime_type
            chunk = base64.b64decode(raw)
            live_request_queue.send_realtime(types.Blob(data=chunk, mime_type=mime_type))
            return

        text = parsed.get("text")
        if not isinstance(text, str) or not text.strip():
            return

        logger.info("live text input session_id=%s text=%s", session_id, text.strip()[:200])
        await self._inject_scene_snapshot_context(session_id=session_id, live_request_queue=live_request_queue)
        live_request_queue.send_content(
            types.Content(
                role="user",
                parts=[types.Part(text=text.strip())],
            )
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
        try:
            state = await self.orchestrator.repo.get_session(session_id)
        except KeyError:
            logger.warning("live seed skipped missing session session_id=%s", session_id)
            return

        beat_spec = await self.orchestrator.repo.get_beat_spec(session_id, state.beat_id)
        if beat_spec is None:
            logger.info("live seed skipped missing beat spec session_id=%s beat_id=%s", session_id, state.beat_id)
            return

        live_request_queue.send_content(
            types.Content(
                role="user",
                parts=[types.Part(text=self._scene_seed_prompt(beat_index=state.beat_index, beat_spec=beat_spec))],
            )
        )
        logger.info("live seed injected session_id=%s beat_id=%s beat_index=%s", session_id, state.beat_id, state.beat_index)

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
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=False)
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

    @staticmethod
    def _scene_seed_prompt(*, beat_index: int, beat_spec: BeatSpec) -> str:
        choices = "\n".join(f"- {choice.label}" for choice in beat_spec.choices)
        return (
            f"Scene {beat_index} context. Objective: {beat_spec.objective}\n"
            f"Setup: {beat_spec.setup}\n"
            f"Escalation: {beat_spec.escalation}\n"
            "Narrate this scene cinematically, then guide the user to choose one path:\n"
            f"{choices}"
        )


def create_live_director_from_env(*, orchestrator: OrchestratorService) -> LiveDirectorService:
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    live_location = os.getenv("WHATIF_LIVE_LOCATION")
    if live_location:
        os.environ["GOOGLE_CLOUD_LOCATION"] = live_location

    settings = LiveDirectorSettings(
        app_name=os.getenv("WHATIF_LIVE_APP_NAME", "whatif-live"),
        model=os.getenv("WHATIF_LIVE_MODEL", "gemini-live-2.5-flash-native-audio"),
        voice_name=os.getenv("WHATIF_LIVE_VOICE", "Aoede"),
        input_audio_mime_type=os.getenv("WHATIF_INPUT_AUDIO_MIME", "audio/pcm;rate=16000"),
        enable_affective_dialog=_env_bool("WHATIF_ENABLE_AFFECTIVE_DIALOG", True),
        enable_proactive_audio=_env_bool("WHATIF_ENABLE_PROACTIVE_AUDIO", False),
    )
    logger.info(
        "live director configured model=%s voice=%s input_audio=%s affective=%s proactive=%s",
        settings.model,
        settings.voice_name,
        settings.input_audio_mime_type,
        settings.enable_affective_dialog,
        settings.enable_proactive_audio,
    )
    return LiveDirectorService(orchestrator=orchestrator, settings=settings)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
