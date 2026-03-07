from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
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
    project: str
    location: str
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


settings = WorkerSettings(
    project=_required_env("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
    asset_bucket=_normalize_bucket(_required_env("WHATIF_ASSET_BUCKET")),
    image_model=os.getenv("WHATIF_IMAGE_MODEL", "imagen-3.0-generate-002"),
    video_model=os.getenv("WHATIF_VIDEO_MODEL", "veo-2.0-generate-001"),
)

client = genai.Client(vertexai=True, project=settings.project, location=settings.location)
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


def _asset_prefix(job: WorkerJob) -> str:
    return f"gs://{settings.asset_bucket}/{job.session_id}/{job.beat_id}/{job.asset_id}/"


def _parse_job_payload(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("pubsub payload must decode to object")
    return parsed


def _generate_storyboard_sync(job: WorkerJob) -> str:
    response = client.models.generate_images(
        model=settings.image_model,
        prompt=job.prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="16:9",
            output_gcs_uri=_asset_prefix(job),
            output_mime_type="image/jpeg",
        ),
    )

    generated_images = response.generated_images or []
    if not generated_images:
        raise RuntimeError("no image returned from image model")

    image = generated_images[0].image
    if image.gcs_uri:
        return image.gcs_uri

    if not image.image_bytes:
        raise RuntimeError("image model returned no gcs uri and no bytes")

    object_name = f"{job.session_id}/{job.beat_id}/{job.asset_id}.jpg"
    blob = storage_client.bucket(settings.asset_bucket).blob(object_name)

    image_bytes = image.image_bytes
    if isinstance(image_bytes, str):
        image_bytes = base64.b64decode(image_bytes)

    blob.upload_from_string(image_bytes, content_type=image.mime_type or "image/jpeg")
    return f"gs://{settings.asset_bucket}/{object_name}"


def _generate_video_sync(job: WorkerJob) -> str:
    operation = client.models.generate_videos(
        model=settings.video_model,
        prompt=job.prompt,
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            aspect_ratio="16:9",
            duration_seconds=4,
            output_gcs_uri=_asset_prefix(job),
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
    if video.uri:
        return video.uri

    if not video.video_bytes:
        raise RuntimeError("video model returned no uri and no bytes")

    object_name = f"{job.session_id}/{job.beat_id}/{job.asset_id}.mp4"
    blob = storage_client.bucket(settings.asset_bucket).blob(object_name)

    video_bytes = video.video_bytes
    if isinstance(video_bytes, str):
        video_bytes = base64.b64decode(video_bytes)

    blob.upload_from_string(video_bytes, content_type=video.mime_type or "video/mp4")
    return f"gs://{settings.asset_bucket}/{object_name}"
