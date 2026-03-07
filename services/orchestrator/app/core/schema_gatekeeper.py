from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.models.contracts import BeatSpec, ExplainResponse, ShotPlan


def validate_beat_spec(payload: dict[str, Any]) -> BeatSpec | None:
    try:
        return BeatSpec.model_validate(payload)
    except ValidationError:
        return None


def validate_shot_plan(payload: dict[str, Any]) -> ShotPlan | None:
    try:
        return ShotPlan.model_validate(payload)
    except ValidationError:
        return None


def validate_explain(payload: dict[str, Any]) -> ExplainResponse | None:
    try:
        return ExplainResponse.model_validate(payload)
    except ValidationError:
        return None
