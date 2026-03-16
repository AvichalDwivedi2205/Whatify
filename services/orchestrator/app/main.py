from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx

from app.api.routes import router
from app.core.agent_runtime import DeterministicAgentRuntime, create_agent_runtime_from_env
from app.core.orchestrator import OrchestratorService
from app.live import create_live_director_from_env
from app.queue.dispatcher import AssetDispatcherProtocol, create_dispatcher_from_env
from app.storage.gcp_repo import create_gcp_repository_from_env
from app.storage.memory_repo import InMemoryRepository
from app.streams.action_bus import ActionBus
from app.streams.caption_bus import CaptionBus

# Load .env from repo root (two levels up from services/orchestrator/)
_repo_root = Path(__file__).resolve().parents[3]
load_dotenv(_repo_root / ".env", override=False)  # don't override already-set env vars


def _configure_logging() -> None:
    level_name = os.getenv("WHATIF_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    log_file = os.getenv("WHATIF_LOG_FILE")

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)

    if log_file:
        log_path = Path(log_file)
        if not log_path.is_absolute():
            log_path = _repo_root / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path = log_path.resolve()
        if not any(
            isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved_path
            for handler in root.handlers
        ):
            file_handler = logging.FileHandler(resolved_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

    root.setLevel(level)
    raw_verbose_logs = os.getenv("WHATIF_VERBOSE_THIRD_PARTY_LOGS", "false").strip().lower()
    if raw_verbose_logs not in {"1", "true", "yes", "on"}:
        for logger_name in (
            "google",
            "google_genai",
            "google_adk",
            "grpc",
            "httpcore",
            "httpx",
            "websockets",
            "uvicorn.access",
        ):
            logging.getLogger(logger_name).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


class LocalStubDispatcher(AssetDispatcherProtocol):
    async def dispatch(self, *, asset_id: str, job, callback_url: str) -> None:  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.18 if job.type.value == "storyboard" else 0.72)
        uri = _stub_storyboard_data_uri(job.prompt, job.shot_id)
        if job.type.value == "hero_video":
            uri = "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4"

        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                callback_url,
                json={
                    "asset_id": asset_id,
                    "session_id": job.session_id,
                    "beat_id": job.beat_id,
                    "shot_id": job.shot_id,
                    "status": "ready",
                    "uri": uri,
                    "generation_time_ms": 180 if job.type.value == "storyboard" else 720,
                },
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    local_stubs = _env_bool("WHATIF_LOCAL_STUBS", False)
    logger.info(
        "starting orchestrator local_stubs=%s callback_url=%s",
        local_stubs,
        os.getenv("WHATIF_ORCHESTRATOR_CALLBACK_URL"),
    )

    repo = InMemoryRepository() if local_stubs else create_gcp_repository_from_env()
    action_bus = ActionBus()
    caption_bus = CaptionBus()
    agents = DeterministicAgentRuntime() if local_stubs else create_agent_runtime_from_env()
    dispatcher = LocalStubDispatcher() if local_stubs else create_dispatcher_from_env()
    callback_url = os.getenv(
        "WHATIF_ORCHESTRATOR_CALLBACK_URL",
        "http://localhost:8080/api/v1/assets/jobs/callback",
    )

    orchestrator = OrchestratorService(
        repo=repo,
        action_bus=action_bus,
        caption_bus=caption_bus,
        agents=agents,
        dispatcher=dispatcher,
        orchestrator_callback_url=callback_url,
    )
    live_director = create_live_director_from_env(orchestrator=orchestrator)

    stop_event = asyncio.Event()
    retry_task = asyncio.create_task(orchestrator.retry_pending_actions_loop(stop_event))
    app.state.orchestrator = orchestrator
    app.state.live_director = live_director
    yield
    logger.info("stopping orchestrator")
    stop_event.set()
    await retry_task


app = FastAPI(title="WhatIf Orchestrator", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _stub_storyboard_data_uri(prompt: str, shot_id: str) -> str:
    headline = (shot_id or "storyboard").replace("_", " ").upper()[:48]
    body = " ".join(prompt.split())[:160]
    svg = f"""
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1280 720'>
  <defs>
    <linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0%' stop-color='#170904'/>
      <stop offset='52%' stop-color='#3a1809'/>
      <stop offset='100%' stop-color='#090504'/>
    </linearGradient>
  </defs>
  <rect width='1280' height='720' fill='url(#bg)'/>
  <circle cx='930' cy='210' r='180' fill='rgba(214,131,45,0.18)'/>
  <circle cx='280' cy='520' r='220' fill='rgba(255,255,255,0.04)'/>
  <rect x='92' y='84' width='1096' height='552' rx='24' fill='rgba(0,0,0,0.24)' stroke='rgba(214,131,45,0.36)'/>
  <text x='132' y='174' fill='#d68b2d' font-size='28' font-family='Georgia, serif' letter-spacing='8'>{headline}</text>
  <text x='132' y='286' fill='#f3ead6' font-size='54' font-family='Georgia, serif'>Generated Storyboard Frame</text>
  <foreignObject x='132' y='340' width='1010' height='210'>
    <div xmlns='http://www.w3.org/1999/xhtml' style='font-family:Georgia,serif;font-size:28px;line-height:1.5;color:#f3ead6;opacity:0.88;'>
      {body}
    </div>
  </foreignObject>
</svg>
""".strip()
    return f"data:image/svg+xml;charset=UTF-8,{quote(svg)}"
