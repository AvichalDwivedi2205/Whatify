from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from app.models.contracts import AssetJob

logger = logging.getLogger(__name__)


class AssetDispatcherProtocol(Protocol):
    async def dispatch(self, *, asset_id: str, job: AssetJob, callback_url: str) -> None: ...


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


def create_dispatcher_from_env() -> PubSubAssetDispatcher:
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT must be set")

    topic_id = os.getenv("WHATIF_PUBSUB_TOPIC", "whatif-asset-jobs")
    return PubSubAssetDispatcher(project_id=project_id, topic_id=topic_id)
