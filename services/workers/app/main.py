from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI
from google import genai
from google.cloud import storage
from google.genai import types
from pydantic import BaseModel
from dotenv import load_dotenv


_repo_root = Path(__file__).resolve().parents[3]
load_dotenv(_repo_root / ".env", override=False)


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


class WorkerJob(BaseModel):
    asset_id: str
    session_id: str
    beat_id: str
    shot_id: str
    asset_type: Literal["storyboard", "hero_video"]
    prompt: str
    orchestrator_callback_url: str


class WorkerResponse(BaseModel):
    ok: bool
    message: str


class PubSubMessage(BaseModel):
    data: str
    attributes: dict[str, str] | None = None
    message_id: str | None = None
    publish_time: str | None = None


class PubSubPushEnvelope(BaseModel):
    message: PubSubMessage
    subscription: str | None = None


@dataclass(slots=True)
class WorkerSettings:
    api_key: str
    additional_api_keys: tuple[str, ...]
    project: str
    asset_bucket: str
    image_model: str
    video_model: str


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _normalize_bucket(bucket: str) -> str:
    return bucket.removeprefix("gs://").strip("/")


def _normalize_image_model(model: str | None) -> str:
    normalized = (model or "").strip()
    if not normalized:
        return "gemini-3.1-flash-image-preview"
    if normalized in {"imagen-3.0-generate-002", "imagen-3.0-fast-generate-001"}:
        return "gemini-3.1-flash-image-preview"
    return normalized


def _normalize_video_model(model: str | None) -> str:
    normalized = (model or "").strip()
    if not normalized:
        return "veo-3.1-fast-generate-preview"
    replacements = {
        "veo-2.0-generate-001": "veo-3.1-fast-generate-preview",
        "veo-3.1-generate-001": "veo-3.1-generate-preview",
        "veo-3.1-fast-generate-001": "veo-3.1-fast-generate-preview",
    }
    return replacements.get(normalized, normalized)


def _load_api_keys() -> tuple[str, tuple[str, ...]]:
    keys: list[str] = []
    primary = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if primary:
        keys.append(primary)

    prefixed_names = sorted(
        name
        for name in os.environ
        if (
            name.startswith("GEMINI_API_KEY")
            or name.startswith("GOOGLE_API_KEY")
        )
        and name not in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}
    )

    for name in prefixed_names:
        value = os.getenv(name, "").strip()
        if value and value not in keys:
            keys.append(value)

    if not keys:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY must be set")
    return keys[0], tuple(keys[1:])


primary_api_key, additional_api_keys = _load_api_keys()

settings = WorkerSettings(
    api_key=primary_api_key,
    additional_api_keys=additional_api_keys,
    project=_required_env("GOOGLE_CLOUD_PROJECT"),
    asset_bucket=_normalize_bucket(_required_env("WHATIF_ASSET_BUCKET")),
    image_model=_normalize_image_model(os.getenv("WHATIF_IMAGE_MODEL")),
    video_model=_normalize_video_model(os.getenv("WHATIF_VIDEO_MODEL")),
)

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
_clients = tuple(
    genai.Client(vertexai=False, api_key=key)
    for key in dict.fromkeys([settings.api_key, *settings.additional_api_keys])
)
_client_lock = threading.Lock()
_client_index = 0
storage_client = storage.Client(project=settings.project)

app = FastAPI(title="WhatIf Workers", version="0.2.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/system/health")
async def system_health() -> dict[str, str]:
    return {
        "service": "whatif-workers",
        "status": "ok",
    }


@app.post("/jobs/generate", response_model=WorkerResponse)
async def generate(job: WorkerJob, background_tasks: BackgroundTasks) -> WorkerResponse:
    logger.info(
        "accepted worker job type=%s session_id=%s beat_id=%s shot_id=%s",
        job.asset_type,
        job.session_id,
        job.beat_id,
        job.shot_id,
    )
    background_tasks.add_task(_process_job, job)
    return WorkerResponse(ok=True, message="accepted")


@app.post("/jobs/pubsub", response_model=WorkerResponse)
async def pubsub_ingest(payload: PubSubPushEnvelope, background_tasks: BackgroundTasks) -> WorkerResponse:
    raw = base64.b64decode(payload.message.data).decode("utf-8")
    job = WorkerJob.model_validate(_parse_job_payload(raw))
    logger.info(
        "accepted pubsub worker job type=%s session_id=%s beat_id=%s shot_id=%s",
        job.asset_type,
        job.session_id,
        job.beat_id,
        job.shot_id,
    )
    background_tasks.add_task(_process_job, job)
    return WorkerResponse(ok=True, message="accepted")


async def _process_job(job: WorkerJob) -> None:
    start = time.monotonic()
    status = "failed"
    uri: str | None = None
    logger.info(
        "processing worker job type=%s session_id=%s beat_id=%s shot_id=%s",
        job.asset_type,
        job.session_id,
        job.beat_id,
        job.shot_id,
    )

    try:
        if job.asset_type == "storyboard":
            uri = await asyncio.to_thread(_generate_storyboard_sync, job)
        else:
            uri = await asyncio.to_thread(_generate_video_sync, job)
        status = "ready"
    except Exception:
        logger.exception(
            "worker job failed type=%s session_id=%s beat_id=%s shot_id=%s",
            job.asset_type,
            job.session_id,
            job.beat_id,
            job.shot_id,
        )
        status = "failed"

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = {
        "asset_id": job.asset_id,
        "session_id": job.session_id,
        "beat_id": job.beat_id,
        "shot_id": job.shot_id,
        "status": status,
        "uri": uri,
        "generation_time_ms": duration_ms,
    }

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        await http_client.post(job.orchestrator_callback_url, json=payload)
    logger.info(
        "worker job completed status=%s type=%s session_id=%s beat_id=%s shot_id=%s duration_ms=%s uri=%s",
        status,
        job.asset_type,
        job.session_id,
        job.beat_id,
        job.shot_id,
        duration_ms,
        uri,
    )


def _parse_job_payload(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("pubsub payload must decode to object")
    return parsed


def _generate_storyboard_sync(job: WorkerJob) -> str:
    response = _next_client().models.generate_content(
        model=settings.image_model,
        contents=[job.prompt],
        config=types.GenerateContentConfig(
            image_config=types.ImageConfig(
                aspect_ratio="16:9",
                image_size="1K",
            )
        ),
    )

    image_bytes, mime_type = _extract_image_bytes(response)
    object_name = _asset_object_name(
        job=job,
        default_extension=".png",
        mime_type=mime_type or "image/png",
    )
    return _upload_asset_bytes(
        object_name=object_name,
        payload=image_bytes,
        content_type=mime_type or "image/png",
    )


def _generate_video_sync(job: WorkerJob) -> str:
    client = _next_client()
    operation = client.models.generate_videos(
        model=settings.video_model,
        prompt=job.prompt,
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            aspect_ratio="16:9",
            duration_seconds=4,
        ),
    )

    while not operation.done:
        time.sleep(5)
        operation = client.operations.get(operation)

    if operation.error:
        raise RuntimeError(str(operation.error))

    response = operation.response or operation.result
    if response is None:
        raise RuntimeError("video operation completed without response")

    generated_videos = response.generated_videos or []
    if not generated_videos:
        raise RuntimeError("no video returned from video model")

    video = generated_videos[0].video
    object_name = _asset_object_name(
        job=job,
        default_extension=".mp4",
        mime_type=video.mime_type or "video/mp4",
    )

    if video.video_bytes:
        video_bytes = video.video_bytes
        if isinstance(video_bytes, str):
            video_bytes = base64.b64decode(video_bytes)
        return _upload_asset_bytes(
            object_name=object_name,
            payload=video_bytes,
            content_type=video.mime_type or "video/mp4",
        )

    if not video.uri:
        raise RuntimeError("video model returned no uri and no bytes")

    video_bytes, mime_type = _download_gemini_asset(video.uri)
    return _upload_asset_bytes(
        object_name=object_name,
        payload=video_bytes,
        content_type=mime_type or video.mime_type or "video/mp4",
    )


def _next_client() -> genai.Client:
    global _client_index
    with _client_lock:
        client = _clients[_client_index % len(_clients)]
        _client_index += 1
        return client


def _extract_image_bytes(response: Any) -> tuple[bytes, str | None]:
    parts = getattr(response, "parts", None)
    if not parts:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []

    for part in parts or []:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None:
            mime_type = getattr(inline_data, "mime_type", None)
            data = getattr(inline_data, "data", None)
            if data is None:
                continue
            if isinstance(data, str):
                return base64.b64decode(data), mime_type
            return data, mime_type

        file_data = getattr(part, "file_data", None)
        if file_data is not None:
            uri = getattr(file_data, "file_uri", None) or getattr(file_data, "uri", None)
            if isinstance(uri, str) and uri:
                return _download_gemini_asset(uri)

    raise RuntimeError("image model returned no image data")


def _download_gemini_asset(uri: str) -> tuple[bytes, str | None]:
    headers: dict[str, str] = {}
    if "generativelanguage.googleapis.com" in uri:
        headers["x-goog-api-key"] = settings.api_key

    response = httpx.get(uri, headers=headers, timeout=120.0, follow_redirects=True)
    response.raise_for_status()
    return response.content, response.headers.get("content-type")


def _asset_object_name(*, job: WorkerJob, default_extension: str, mime_type: str) -> str:
    extension = mimetypes.guess_extension(mime_type.split(";", 1)[0].strip()) or default_extension
    return f"{job.session_id}/{job.beat_id}/{job.asset_id}{extension}"


def _upload_asset_bytes(*, object_name: str, payload: bytes, content_type: str) -> str:
    blob = storage_client.bucket(settings.asset_bucket).blob(object_name)
    blob.upload_from_string(payload, content_type=content_type)
    return f"gs://{settings.asset_bucket}/{object_name}"
