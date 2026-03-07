from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from app.core.orchestrator import OrchestratorService
from app.live import LiveDirectorService
from app.models.contracts import (
    AckRequest,
    ActionAckResponse,
    AssetCallbackRequest,
    AssetExplainRequest,
    BeginSessionRequest,
    ChoiceRequest,
    DirectorSignalRequest,
    GenericResponse,
    InterleavedProofResponse,
    InterruptRequest,
    SessionStateResponse,
    StartSessionRequest,
    StartSessionResponse,
    TimelineResponse,
)

router = APIRouter(prefix="/api/v1")


def get_orchestrator(request: Request) -> OrchestratorService:
    return cast(OrchestratorService, request.app.state.orchestrator)


def get_live_director_state(websocket: WebSocket) -> LiveDirectorService:
    return cast(LiveDirectorService, websocket.app.state.live_director)


@router.get("/system/health")
async def system_health() -> dict[str, str]:
    return {
        "service": "whatif-orchestrator",
        "status": "ok",
    }


@router.post("/session/start", response_model=StartSessionResponse)
async def start_session(
    request: StartSessionRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> StartSessionResponse:
    return await orchestrator.start_session(request)


@router.post("/session/{session_id}/begin", response_model=GenericResponse)
async def begin_session(
    session_id: str,
    request: BeginSessionRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> GenericResponse:
    return await orchestrator.begin_session(session_id, request)


@router.post("/session/{session_id}/continue", response_model=GenericResponse)
async def continue_session(
    session_id: str,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> GenericResponse:
    return await orchestrator.continue_session(session_id)


@router.post("/session/{session_id}/interrupt", response_model=GenericResponse)
async def interrupt(
    session_id: str,
    request: InterruptRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> GenericResponse:
    return await orchestrator.interrupt(session_id, request)


@router.post("/session/{session_id}/asset-explain", response_model=GenericResponse)
async def asset_explain(
    session_id: str,
    request: AssetExplainRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> GenericResponse:
    return await orchestrator.asset_explain(session_id, request)


@router.post("/session/{session_id}/choice", response_model=GenericResponse)
async def choose(
    session_id: str,
    request: ChoiceRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> GenericResponse:
    return await orchestrator.choose(session_id, request)


@router.post("/session/{session_id}/ack", response_model=ActionAckResponse)
async def ack(
    session_id: str,
    request: AckRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> ActionAckResponse:
    return await orchestrator.ack_action(session_id, request)


@router.get("/session/{session_id}/state", response_model=SessionStateResponse)
async def get_state(
    session_id: str,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> SessionStateResponse:
    return await orchestrator.get_state(session_id)


@router.get("/session/{session_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    session_id: str,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> TimelineResponse:
    return await orchestrator.get_timeline(session_id)


@router.get("/session/{session_id}/interleaved-proof", response_model=InterleavedProofResponse)
async def get_interleaved_proof(
    session_id: str,
    beat_id: str | None = None,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> InterleavedProofResponse:
    return await orchestrator.get_interleaved_proof(session_id=session_id, beat_id=beat_id)


@router.post("/assets/jobs/callback", response_model=GenericResponse)
async def asset_callback(
    request: AssetCallbackRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> GenericResponse:
    return await orchestrator.asset_callback(request)


@router.post("/session/{session_id}/director-signal", response_model=GenericResponse)
async def director_signal(
    session_id: str,
    request: DirectorSignalRequest,
    orchestrator: OrchestratorService = Depends(get_orchestrator),
) -> GenericResponse:
    return await orchestrator.handle_director_signal(session_id, request)


@router.websocket("/session/{session_id}/actions")
async def actions_stream(websocket: WebSocket, session_id: str) -> None:
    orchestrator = websocket.app.state.orchestrator
    await orchestrator.action_bus.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await orchestrator.action_bus.disconnect(session_id, websocket)


@router.websocket("/session/{session_id}/captions")
async def captions_stream(websocket: WebSocket, session_id: str) -> None:
    orchestrator = websocket.app.state.orchestrator
    await orchestrator.caption_bus.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await orchestrator.caption_bus.disconnect(session_id, websocket)


@router.websocket("/session/{session_id}/live")
async def live_stream(websocket: WebSocket, session_id: str) -> None:
    user_id = str(websocket.query_params.get("user_id", "voice-user"))
    live_director = get_live_director_state(websocket)
    await live_director.handle_websocket(websocket, session_id=session_id, user_id=user_id)
