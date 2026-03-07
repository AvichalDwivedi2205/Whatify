from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

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
        if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved_path for handler in root.handlers):
            file_handler = logging.FileHandler(resolved_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

    root.setLevel(level)


_configure_logging()
logger = logging.getLogger(__name__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.agent_runtime import DeterministicAgentRuntime, create_agent_runtime_from_env
from app.core.orchestrator import OrchestratorService
from app.live import create_live_director_from_env
from app.queue.dispatcher import AssetDispatcherProtocol, create_dispatcher_from_env
from app.storage.gcp_repo import create_gcp_repository_from_env
from app.storage.memory_repo import InMemoryRepository
from app.streams.action_bus import ActionBus
from app.streams.caption_bus import CaptionBus


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


class LocalNoopDispatcher(AssetDispatcherProtocol):
    async def dispatch(self, *, asset_id: str, job, callback_url: str) -> None:  # type: ignore[no-untyped-def]
        _ = asset_id
        _ = job
        _ = callback_url
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    local_stubs = _env_bool("WHATIF_LOCAL_STUBS", False)
    logger.info("starting orchestrator local_stubs=%s callback_url=%s", local_stubs, os.getenv("WHATIF_ORCHESTRATOR_CALLBACK_URL"))

    repo = InMemoryRepository() if local_stubs else create_gcp_repository_from_env()
    action_bus = ActionBus()
    caption_bus = CaptionBus()
    agents = DeterministicAgentRuntime() if local_stubs else create_agent_runtime_from_env()
    dispatcher = LocalNoopDispatcher() if local_stubs else create_dispatcher_from_env()
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
