from __future__ import annotations

import pytest

from app.core.agent_runtime import DeterministicAgentRuntime
from app.core.orchestrator import OrchestratorService
from app.live.director import LiveDirectorService
from app.live.director import LiveDirectorSettings
from app.live.director import _normalize_live_model
from app.queue.dispatcher import AssetDispatcherProtocol
from app.storage.memory_repo import InMemoryRepository
from app.streams.action_bus import ActionBus
from app.streams.caption_bus import CaptionBus


class NoopDispatcher(AssetDispatcherProtocol):
    async def dispatch(self, *, asset_id: str, job, callback_url: str) -> None:  # type: ignore[no-untyped-def]
        _ = asset_id
        _ = job
        _ = callback_url


class RecordingLiveRequestQueue:
    def __init__(self) -> None:
        self.contents: list[object] = []

    def send_content(self, content: object) -> None:
        self.contents.append(content)


def _build_service() -> LiveDirectorService:
    orchestrator = OrchestratorService(
        repo=InMemoryRepository(),
        action_bus=ActionBus(),
        caption_bus=CaptionBus(),
        agents=DeterministicAgentRuntime(),
        dispatcher=NoopDispatcher(),
        orchestrator_callback_url="http://localhost:8080/api/v1/assets/jobs/callback",
    )
    return LiveDirectorService(
        orchestrator=orchestrator,
        settings=LiveDirectorSettings(
            app_name="whatif-live",
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            voice_name="Aoede",
            input_audio_mime_type="audio/pcm;rate=16000",
            enable_affective_dialog=True,
            enable_proactive_audio=False,
            max_retries=3,
            retry_base_delay_seconds=0.75,
            retry_max_delay_seconds=4.0,
        ),
    )


def test_bounded_snapshot_truncates_large_payload() -> None:
    payload = {"text": "x" * 5000}
    bounded = LiveDirectorService._bounded_snapshot(payload, max_chars=120)
    assert bounded.get("truncated") is True
    assert isinstance(bounded.get("snippet"), str)


def test_normalize_live_model_pins_aliases() -> None:
    assert _normalize_live_model(None) == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert _normalize_live_model("gemini-2.5-flash-native-audio-latest") == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert _normalize_live_model("gemini-live-2.5-flash-native-audio") == "gemini-2.5-flash-native-audio-preview-12-2025"


def test_retry_delay_applies_only_to_retryable_errors() -> None:
    service = _build_service()

    class DummyError(Exception):
        def __init__(self, code: int) -> None:
            super().__init__(str(code))
            self.code = code

    assert service._retry_delay_seconds(0, DummyError(1011)) == pytest.approx(0.75)
    assert service._retry_delay_seconds(1, DummyError(1011)) == pytest.approx(1.5)
    assert service._retry_delay_seconds(3, DummyError(1011)) is None
    assert service._retry_delay_seconds(0, DummyError(1008)) is None


def test_sanitize_spoken_output_filters_onboarding_meta_and_control_tokens() -> None:
    service = _build_service()
    service._scene_snapshots["s1"] = {"phase": "onboarding"}

    assert service._sanitize_spoken_output("s1", "<ctrl46><ctrl46>") is None
    assert (
        service._sanitize_spoken_output(
            "s1",
            "I'm zeroing in on the critical requirement: deliver the intro verbatim.",
        )
        is None
    )
    assert (
        service._sanitize_spoken_output(
            "s1",
            'I have notes. Welcome to WhatIf. Say the one moment that breaks history, and I will show you the world that follows." My delivery will be warm and cinematic.',
        )
        == "Welcome to WhatIf. Say the one moment that breaks history, and I will show you the world that follows."
    )


def test_sanitize_spoken_output_strips_story_control_tokens() -> None:
    service = _build_service()
    service._scene_snapshots["s1"] = {"phase": "acting"}

    assert (
        service._sanitize_spoken_output(
            "s1",
            '<signal_emotion_target target="tense"/>Flight Director Gene Kranz holds his breath.',
        )
        == "Flight Director Gene Kranz holds his breath."
    )


@pytest.mark.asyncio
async def test_replay_pending_text_turn_requeues_snapshot_and_prompt() -> None:
    service = _build_service()
    queue = RecordingLiveRequestQueue()
    service._scene_snapshots["s1"] = {"phase": "acting", "moment_caption": "Act I"}

    await service._handle_text_payload(
        session_id="s1",
        parsed={"text": "Describe this exact scene."},
        live_request_queue=queue,  # type: ignore[arg-type]
    )

    initial_turns = [content.parts[0].text for content in queue.contents]  # type: ignore[attr-defined]
    assert initial_turns[-1] == "Describe this exact scene."

    queue.contents.clear()
    replayed = await service._replay_pending_text_turn(
        session_id="s1",
        live_request_queue=queue,  # type: ignore[arg-type]
    )

    replayed_turns = [content.parts[0].text for content in queue.contents]  # type: ignore[attr-defined]
    assert replayed is True
    assert replayed_turns[0] == LiveDirectorService._snapshot_summary(service._scene_snapshots["s1"])
    assert replayed_turns[1] == "Describe this exact scene."
