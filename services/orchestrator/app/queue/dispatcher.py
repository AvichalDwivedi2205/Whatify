from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Protocol
from urllib.parse import quote

import httpx
from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from app.models.contracts import AssetJob
from app.models.enums import AssetType

logger = logging.getLogger(__name__)


class AssetDispatcherProtocol(Protocol):
    async def dispatch(self, *, asset_id: str, job: AssetJob, callback_url: str) -> None: ...


class HttpAssetDispatcher:
    def __init__(self, *, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def dispatch(self, *, asset_id: str, job: AssetJob, callback_url: str) -> None:
        payload = {
            "asset_id": asset_id,
            "session_id": job.session_id,
            "beat_id": job.beat_id,
            "shot_id": job.shot_id,
            "asset_type": job.type.value,
            "prompt": job.prompt,
            "orchestrator_callback_url": callback_url,
        }
        target = f"{self.base_url}/jobs/generate"
        logger.info(
            "posting asset job asset_id=%s session_id=%s beat_id=%s shot_id=%s type=%s url=%s",
            asset_id,
            job.session_id,
            job.beat_id,
            job.shot_id,
            job.type.value,
            target,
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(target, json=payload)
            response.raise_for_status()


class FallbackAssetDispatcher:
    def __init__(self, *, primary: AssetDispatcherProtocol | None = None) -> None:
        self.primary = primary

    async def dispatch(self, *, asset_id: str, job: AssetJob, callback_url: str) -> None:
        if self.primary is not None:
            try:
                await self.primary.dispatch(asset_id=asset_id, job=job, callback_url=callback_url)
                return
            except Exception:
                logger.exception(
                    "asset dispatch failed; using local fallback asset_id=%s session_id=%s beat_id=%s shot_id=%s type=%s",
                    asset_id,
                    job.session_id,
                    job.beat_id,
                    job.shot_id,
                    job.type.value,
                )

        uri = _stub_asset_uri(job)
        payload = {
            "asset_id": asset_id,
            "session_id": job.session_id,
            "beat_id": job.beat_id,
            "shot_id": job.shot_id,
            "status": "ready",
            "uri": uri,
            "generation_time_ms": 220 if job.type == AssetType.STORYBOARD else 720,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(callback_url, json=payload)
            response.raise_for_status()
        logger.info(
            "local fallback asset emitted asset_id=%s session_id=%s beat_id=%s shot_id=%s type=%s uri=%s",
            asset_id,
            job.session_id,
            job.beat_id,
            job.shot_id,
            job.type.value,
            uri[:120],
        )


class PubSubAssetDispatcher:
    def __init__(self, *, project_id: str, topic_id: str) -> None:
        self.publisher = pubsub_v1.PublisherClient()
        self.topic_path = self.publisher.topic_path(project_id, topic_id)
        self._ensure_topic_exists()

    async def dispatch(self, *, asset_id: str, job: AssetJob, callback_url: str) -> None:
        payload = {
            "asset_id": asset_id,
            "session_id": job.session_id,
            "beat_id": job.beat_id,
            "shot_id": job.shot_id,
            "asset_type": job.type.value,
            "prompt": job.prompt,
            "orchestrator_callback_url": callback_url,
        }

        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        def publish_sync() -> None:
            future = self.publisher.publish(self.topic_path, data)
            future.result(timeout=20)

        logger.info(
            "publishing asset job asset_id=%s session_id=%s beat_id=%s shot_id=%s type=%s topic=%s",
            asset_id,
            job.session_id,
            job.beat_id,
            job.shot_id,
            job.type.value,
            self.topic_path,
        )
        await asyncio.to_thread(publish_sync)

    def _ensure_topic_exists(self) -> None:
        try:
            self.publisher.create_topic(request={"name": self.topic_path})
        except AlreadyExists:
            return


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


def _stub_asset_uri(job: AssetJob) -> str:
    if job.type == AssetType.HERO_VIDEO:
        return "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4"
    return _stub_storyboard_data_uri(job.prompt, job.shot_id)


def create_dispatcher_from_env() -> AssetDispatcherProtocol:
    worker_url = (os.getenv("WHATIF_WORKER_URL") or "").strip()
    local_env = (os.getenv("WHATIF_ENV") or "").strip().lower() == "local"
    allow_local_fallback = _env_bool("WHATIF_LOCAL_ASSET_FALLBACK", local_env)
    if worker_url:
        primary = HttpAssetDispatcher(base_url=worker_url)
        return FallbackAssetDispatcher(primary=primary) if allow_local_fallback else primary

    if local_env:
        local_worker_url = (os.getenv("WHATIF_LOCAL_WORKER_URL") or "http://localhost:8090").strip()
        return FallbackAssetDispatcher(
            primary=HttpAssetDispatcher(base_url=local_worker_url, timeout_seconds=2.5)
        )

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT must be set")

    topic_id = os.getenv("WHATIF_PUBSUB_TOPIC", "whatif-asset-jobs")
    return PubSubAssetDispatcher(project_id=project_id, topic_id=topic_id)
