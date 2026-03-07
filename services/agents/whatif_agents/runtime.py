from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AgentRoles:
    """Container for ADK agent role configs.

    This module keeps role definitions separate from the orchestrator so ADK
    execution can evolve independently.
    """

    model: str = "gemini-2.5-pro"

    def planner_prompt(self) -> str:
        return (
            "You are Story Planner. Return strict JSON BeatSpec only. "
            "No markdown or prose. Respect branch continuity and cinematic pacing."
        )

    def consistency_prompt(self) -> str:
        return (
            "You are Canon & Consistency. Validate BeatSpec and return ConsistencyReport JSON. "
            "If no issues, set ok=true and fixes=[]."
        )

    def explainer_prompt(self) -> str:
        return (
            "You are Explainer. Answer from supplied causal chain/events only. "
            "Return spoken_answer + overlay_chain JSON."
        )

    def historian_prompt(self) -> str:
        return (
            "You are Historian. Return concise reality anchor cards and comparison points with citations."
        )

    def shot_planner_prompt(self) -> str:
        return (
            "You are Shot Planner. Convert BeatSpec to ShotPlan with 3-6 storyboard shots and max 2 hero shots."
        )

    def safety_prompt(self) -> str:
        return "You are Safety Editor. Rewrite unsafe content while preserving intent."

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "planner_prompt": self.planner_prompt(),
            "consistency_prompt": self.consistency_prompt(),
            "explainer_prompt": self.explainer_prompt(),
            "historian_prompt": self.historian_prompt(),
            "shot_planner_prompt": self.shot_planner_prompt(),
            "safety_prompt": self.safety_prompt(),
        }
