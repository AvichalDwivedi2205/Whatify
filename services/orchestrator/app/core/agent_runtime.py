from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any, Protocol, TypeVar

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from pydantic import BaseModel, ValidationError

from app.models.contracts import (
    BeatSpec,
    BranchRuleUpdate,
    Choice,
    ComparisonPoint,
    ConsistencyFix,
    ConsistencyReport,
    ExplainResponse,
    InterleavedBlock,
    InterleavedGeneration,
    OverlayEdge,
    RealityAnchorCard,
    RealityResponse,
    Shot,
    ShotPlan,
)
from app.models.enums import InterleavedTrigger
from app.models.state import TimelineEdge
from app.utils.id import stable_id

T = TypeVar("T", bound=BaseModel)


class AgentRuntimeProtocol(Protocol):
    async def plan_beat(
        self,
        *,
        session_id: str,
        beat_id: str,
        beat_index: int,
        context: dict[str, Any],
    ) -> BeatSpec: ...

    async def check_consistency(self, beat_spec: BeatSpec) -> ConsistencyReport: ...

    async def plan_shots(self, beat_spec: BeatSpec, style: str = "cinematic-war-room") -> ShotPlan: ...

    async def generate_interleaved_story(
        self,
        *,
        session_id: str,
        beat_id: str,
        beat_index: int,
        beat_spec: BeatSpec,
        trigger: InterleavedTrigger,
        question: str | None = None,
    ) -> InterleavedGeneration: ...

    async def explain(self, question: str, chain: list[TimelineEdge], beat_context: str) -> ExplainResponse: ...

    async def reality_compare(self, topic: str) -> RealityResponse: ...

    async def safety_rewrite(self, text: str) -> str: ...

    async def make_edge(self, beat_id: str, choice_id: str, event_ids: list[str]) -> TimelineEdge: ...


@dataclass(slots=True)
class DeterministicAgentRuntime:
    """Fallback runtime for tests and offline development."""

    async def plan_beat(
        self,
        *,
        session_id: str,
        beat_id: str,
        beat_index: int,
        context: dict[str, Any],
    ) -> BeatSpec:
        divergence_point = context.get("divergence_point", "history diverges")
        active_entities = [
            "city_alexandria",
            "scholar_council",
            "imperial_emissary",
            "harbor_guild",
        ]
        choices = [
            Choice(
                choice_id="c1",
                label="Fund rapid expansion of archival schools",
                consequence_hint="Knowledge spreads quickly and threatens political balance.",
            ),
            Choice(
                choice_id="c2",
                label="Centralize control under a royal curator",
                consequence_hint="Efficiency rises but dissent and censorship increase.",
            ),
            Choice(
                choice_id="c3",
                label="Open the archives to foreign delegates",
                consequence_hint="Diplomatic gains with higher espionage risk.",
            ),
        ]
        return BeatSpec(
            beat_id=beat_id,
            objective=f"Drive the alternate timeline forward for beat {beat_index}.",
            setup=(
                f"Divergence anchor: {divergence_point}. "
                "The city prepares for a consequential decision."
            ),
            escalation=(
                "Competing factions challenge the future of knowledge governance "
                "while pressure mounts from neighboring powers."
            ),
            act_title=f"The Turning of Act {beat_index}",
            act_time_label=f"Year {400 + (beat_index * 120)} CE",
            narration_script=(
                "Narrate this act as a cinematic sequence with tension, consequence, and vivid sensory detail."
            ),
            intermission_line="The act closes. Do you want to continue?",
            choices=choices,
            consequence_seed=(
                "The selected policy reshapes alliances, technology transfer, "
                "and legitimacy of the ruling bloc."
            ),
            transition_hook="A new coalition forms in the shadows as the decision takes effect.",
            active_entities=active_entities,
            branch_rule_updates=[
                BranchRuleUpdate(
                    rule_id=f"rule_{beat_index}",
                    statement=(
                        "The Alexandrian archive network remains operational "
                        "and politically central."
                    ),
                    confidence=0.82,
                    constraints=["Archive continuity must hold across future beats."],
                )
            ],
        )

    async def check_consistency(self, beat_spec: BeatSpec) -> ConsistencyReport:
        fixes: list[ConsistencyFix] = []
        warnings: list[str] = []

        if "as an ai" in beat_spec.setup.lower():
            fixes.append(
                ConsistencyFix(
                    field="setup",
                    replacement=beat_spec.setup.replace("As an AI", "In this timeline"),
                    reason="Removed non-diegetic phrasing",
                )
            )

        if len(beat_spec.choices) < 2:
            warnings.append("Insufficient choices; additional choices should be injected.")

        return ConsistencyReport(ok=len(warnings) == 0, fixes=fixes, continuity_warnings=warnings)

    async def plan_shots(self, beat_spec: BeatSpec, style: str = "cinematic-war-room") -> ShotPlan:
        base_shots = [
            Shot(
                shot_id=f"{beat_spec.beat_id}_s1",
                framing="wide establishing",
                composition="the altered city and its intellectual center under restless torchlight",
                camera_motion="slow dolly-in",
                prompt=(
                    f"{style}, alternate-history epic, historically grounded establishing shot, "
                    f"architectural scale, lived-in materials, no typography, no UI, {beat_spec.setup}"
                ),
                priority="high",
                reuse_tags=["city", "command_room"],
            ),
            Shot(
                shot_id=f"{beat_spec.beat_id}_s2",
                framing="medium ensemble",
                composition="inventors, scribes, and patrons working through the first visible change",
                camera_motion="subtle handheld tension",
                prompt=(
                    f"{style}, historically specific workshop tableau, human action in progress, "
                    f"rich period detail, no typography, no captions, {beat_spec.setup}"
                ),
                priority="medium",
                reuse_tags=["workshop", "craft"],
            ),
            Shot(
                shot_id=f"{beat_spec.beat_id}_s3",
                framing="medium wide",
                composition="the new idea spreading into public life with urgency and scale",
                camera_motion="tracking lateral move",
                prompt=(
                    f"{style}, public consequence, crowds, motion, institutions reacting, "
                    f"historically grounded, cinematic depth, no typography, {beat_spec.escalation}"
                ),
                priority="high",
                reuse_tags=["public_square", "spread"],
            ),
            Shot(
                shot_id=f"{beat_spec.beat_id}_s4",
                framing="medium close",
                composition="power brokers realizing the cost of the altered timeline",
                camera_motion="measured push-in",
                prompt=(
                    f"{style}, elite reaction, political tension, dramatic faces, material realism, "
                    f"no typography, no interface elements, {beat_spec.escalation}"
                ),
                priority="medium",
                reuse_tags=["council", "reaction"],
            ),
            Shot(
                shot_id=f"{beat_spec.beat_id}_s5",
                framing="close-up",
                composition="the decisive object or gesture that crystallizes the act's consequence",
                camera_motion="micro push-in",
                prompt=(
                    f"{style}, decisive consequence, dramatic close-up, tactile detail, high contrast, "
                    f"historically grounded, no typography, {beat_spec.consequence_seed}"
                ),
                priority="high",
                reuse_tags=["decision", "map"],
            ),
        ]
        return ShotPlan(shots=base_shots, hero_shots=[base_shots[-1]])

    async def generate_interleaved_story(
        self,
        *,
        session_id: str,
        beat_id: str,
        beat_index: int,
        beat_spec: BeatSpec,
        trigger: InterleavedTrigger,
        question: str | None = None,
    ) -> InterleavedGeneration:
        prompt_suffix = f"Prompt focus: {question}" if question else beat_spec.escalation
        scene_texts = [
            beat_spec.setup,
            "The first proof of change becomes visible in public life and forces witnesses to react.",
            beat_spec.escalation,
            beat_spec.consequence_seed,
        ]
        blocks: list[InterleavedBlock] = []
        for index, text in enumerate(scene_texts):
            blocks.append(
                InterleavedBlock(
                    part_order=index * 2,
                    kind="text",
                    text=f"Scene {index + 1}. {text}",
                )
            )
            blocks.append(
                InterleavedBlock(
                    part_order=index * 2 + 1,
                    kind="image",
                    uri=f"gs://whatif-synthetic/{session_id}/{beat_id}/interleaved-frame-{index + 1}.jpg",
                    mime_type="image/jpeg",
                )
            )
        return InterleavedGeneration(
            run_id=stable_id("ilv", f"{session_id}:{beat_id}:{trigger.value}:{beat_index}"),
            session_id=session_id,
            beat_id=beat_id,
            trigger=trigger,
            model_id="deterministic-interleaved-runtime",
            request_id=stable_id("req", f"{session_id}:{beat_id}:{trigger.value}:{prompt_suffix}"),
            blocks=blocks,
        )

    async def explain(self, question: str, chain: list[TimelineEdge], beat_context: str) -> ExplainResponse:
        edges = [
            OverlayEdge(
                edge_id=edge.edge_id,
                from_node=edge.from_node,
                to_node=edge.to_node,
                justification=edge.justification,
                supporting_event_ids=edge.supporting_event_ids,
                confidence=edge.confidence,
            )
            for edge in chain
        ]
        summary = (
            f"Because earlier choices changed incentives and alliances. {beat_context} "
            f"Most direct driver: {chain[-1].justification if chain else 'initial divergence anchor.'}"
        )
        return ExplainResponse(
            spoken_answer=f"{summary} (Question: {question})",
            overlay_chain=edges,
            groundedness_flags={"event_grounded": True, "contains_inference": bool(chain)},
        )

    async def reality_compare(self, topic: str) -> RealityResponse:
        return RealityResponse(
            cards=[
                RealityAnchorCard(
                    title="Real history anchor",
                    bullet=(
                        "In real history, the Library of Alexandria declined over centuries and "
                        "knowledge diffusion followed different institutional centers."
                    ),
                    citation="Historical synthesis (curated)",
                )
            ],
            comparison_points=[
                ComparisonPoint(
                    changed_fact="Alexandria remained continuously institutionalized.",
                    real_fact="Institutional continuity fragmented across regions.",
                )
            ],
        )

    async def safety_rewrite(self, text: str) -> str:
        blocked = ["graphic violence", "hate speech"]
        sanitized = text
        for token in blocked:
            sanitized = sanitized.replace(token, "redacted")
        return sanitized

    async def make_edge(self, beat_id: str, choice_id: str, event_ids: list[str]) -> TimelineEdge:
        return TimelineEdge(
            edge_id=stable_id("edge", f"{beat_id}:{choice_id}:{'-'.join(event_ids)}"),
            from_node=beat_id,
            to_node=f"{beat_id}:consequence:{choice_id}",
            edge_type="CHOICE_CAUSES",
            justification="Selected policy redirected institutional power.",
            supporting_event_ids=event_ids,
            confidence=0.84,
        )


class SafetyRewriteResponse(BaseModel):
    rewritten_text: str


@dataclass(slots=True)
class GeminiAgentRuntime:
    api_key: str
    model: str
    interleaved_model: str = "gemini-3.1-flash-image-preview"
    interleaved_fallback_model: str | None = None
    max_attempts: int = 3
    retry_base_delay_seconds: float = 0.8
    additional_api_keys: tuple[str, ...] = ()
    client: genai.Client = field(init=False, repr=False)
    _clients: tuple[genai.Client, ...] = field(init=False, repr=False)
    _client_lock: Any = field(init=False, repr=False)
    _client_index: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
        keys = tuple(dict.fromkeys([self.api_key, *self.additional_api_keys]))
        self._clients = tuple(genai.Client(vertexai=False, api_key=key) for key in keys if key)
        if not self._clients:
            raise RuntimeError("at least one Gemini API key is required")
        self.client = self._clients[0]
        self._client_lock = threading.Lock()

    async def plan_beat(
        self,
        *,
        session_id: str,
        beat_id: str,
        beat_index: int,
        context: dict[str, Any],
    ) -> BeatSpec:
        payload = {
            "session_id": session_id,
            "beat_id": beat_id,
            "beat_index": beat_index,
            "context": context,
            "constraints": {
                "choices_min": 2,
                "choices_max": 4,
                "cinematic": True,
                "avoid_meta": True,
                "include_act_metadata": ["act_title", "act_time_label", "narration_script", "intermission_line"],
            },
        }
        prompt = (
            "You are Story Planner for WhatIf. Output ONLY valid JSON matching BeatSpec. "
            "No markdown. No additional keys. Keep language cinematic but concise. "
            "Beat 1 establishes the altered world. Every later beat must continue directly from the previous beat's "
            "transition hook, unresolved consequence, and active entities. Do not reset the timeline, replay the original "
            "divergence as if it just happened, or repeat the previous beat's opening image in new words.\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=True)}"
        )
        return await self._generate_typed(prompt=prompt, schema_model=BeatSpec, temperature=0.45)

    async def check_consistency(self, beat_spec: BeatSpec) -> ConsistencyReport:
        prompt = (
            "You are Canon & Consistency Agent. Check this BeatSpec for continuity conflicts. "
            "Return only ConsistencyReport JSON. If clean set ok=true.\n"
            f"BEAT_SPEC:\n{beat_spec.model_dump_json()}"
        )
        return await self._generate_typed(prompt=prompt, schema_model=ConsistencyReport, temperature=0.2)

    async def plan_shots(self, beat_spec: BeatSpec, style: str = "cinematic-war-room") -> ShotPlan:
        payload = {
            "style": style,
            "beat_spec": beat_spec.model_dump(mode="json"),
            "constraints": {
                "shots_min": 3,
                "shots_max": 5,
                "hero_shots_max": 1,
            },
        }
        prompt = (
            "You are Shot Planner Agent. Generate cinematic shot plan JSON only. "
            "Respect max hero shots and keep prompts production-ready for image/video models. "
            "Plan a visual progression from opening image to consequence, using 3 to 5 distinct shots. "
            "Each prompt must be historically grounded, visually specific, and ready for image generation: "
            "include subject, action, setting, materials, lighting, mood, camera framing or motion, and era detail. "
            "Forbid visible typography, subtitles, UI elements, watermarks, split screens, or collage layouts.\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=True)}"
        )
        return await self._generate_typed(prompt=prompt, schema_model=ShotPlan, temperature=0.5)

    async def generate_interleaved_story(
        self,
        *,
        session_id: str,
        beat_id: str,
        beat_index: int,
        beat_spec: BeatSpec,
        trigger: InterleavedTrigger,
        question: str | None = None,
    ) -> InterleavedGeneration:
        payload = {
            "session_id": session_id,
            "beat_id": beat_id,
            "beat_index": beat_index,
            "trigger": trigger.value,
            "question": question,
            "act_title": beat_spec.act_title,
            "act_time_label": beat_spec.act_time_label,
            "objective": beat_spec.objective,
            "setup": beat_spec.setup,
            "escalation": beat_spec.escalation,
            "consequence_seed": beat_spec.consequence_seed,
            "narration_script": beat_spec.narration_script,
        }
        prompt = (
            "You are an interleaved cinematic storyteller. "
            "Return a single response with mixed text and image parts in temporal order. "
            "Requirements: no JSON wrapper. Return 3 to 5 scene movements. For each movement, output one short text segment "
            "sized for an on-screen narration card, immediately followed by one matching image segment in temporal order. "
            "The text should describe the exact image moment first, then the historical consequence unfolding in that same beat. "
            "Each image must be visually distinct and depict a different stage of the act. Keep the text vivid, concrete, "
            "and scene-first. Continue from the current beat's consequence chain instead of re-explaining the timeline from scratch. "
            "Avoid repeated phrases or duplicated scene openings across consecutive blocks. "
            "No markdown, no headings, no meta narration, no future-act spoilers, no visible typography in images. "
            "Never return fewer than one text segment and one image segment.\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=True)}"
        )

        candidate_models = [self.interleaved_model]
        fallback = (self.interleaved_fallback_model or "").strip()
        if fallback and fallback not in candidate_models:
            candidate_models.append(fallback)

        last_error: Exception | None = None
        for candidate_model in candidate_models:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = await asyncio.to_thread(
                        self._generate_interleaved_once,
                        prompt=prompt,
                        model_id=candidate_model,
                    )
                    request_id, blocks = _extract_interleaved_parts(response)
                    return InterleavedGeneration(
                        run_id=stable_id("ilv", f"{session_id}:{beat_id}:{trigger.value}:{request_id}"),
                        session_id=session_id,
                        beat_id=beat_id,
                        trigger=trigger,
                        model_id=candidate_model,
                        request_id=request_id,
                        blocks=blocks,
                    )
                except ClientError as exc:
                    last_error = exc
                    if self._should_retry(exc, attempt):
                        await asyncio.sleep(self.retry_base_delay_seconds * attempt)
                        continue
                    break
                except RuntimeError as exc:
                    last_error = exc
                    if attempt < self.max_attempts:
                        await asyncio.sleep(self.retry_base_delay_seconds * attempt)
                        continue
                    break

        models_joined = ", ".join(candidate_models)
        raise RuntimeError(f"interleaved generation failed for models: {models_joined}") from last_error

    async def explain(self, question: str, chain: list[TimelineEdge], beat_context: str) -> ExplainResponse:
        payload = {
            "question": question,
            "beat_context": beat_context,
            "chain": [
                {
                    "edge_id": edge.edge_id,
                    "from_node": edge.from_node,
                    "to_node": edge.to_node,
                    "edge_type": edge.edge_type,
                    "justification": edge.justification,
                    "supporting_event_ids": edge.supporting_event_ids,
                    "confidence": edge.confidence,
                }
                for edge in chain
            ],
            "rules": {
                "ground_from_chain_only": True,
                "spoken_answer_max_words": 90,
            },
        }
        prompt = (
            "You are Explainer Agent. Answer using provided causal chain only. "
            "Return ExplainResponse JSON only and set groundedness_flags correctly.\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=True)}"
        )
        return await self._generate_typed(prompt=prompt, schema_model=ExplainResponse, temperature=0.2)

    async def reality_compare(self, topic: str) -> RealityResponse:
        payload = {
            "topic": topic,
            "instructions": "Provide concise factual anchor cards and timeline comparison points.",
        }
        prompt = (
            "You are Historian Agent. Return only RealityResponse JSON. "
            "Include citations when possible. Do not include fabricated citation URLs.\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=True)}"
        )
        return await self._generate_typed(prompt=prompt, schema_model=RealityResponse, temperature=0.3)

    async def safety_rewrite(self, text: str) -> str:
        payload = {
            "text": text,
            "policy": "Rewrite unsafe content while preserving scene intent and pacing.",
        }
        prompt = (
            "You are Safety Editor Agent. Return only JSON with rewritten_text field. "
            "If text is already safe, return unchanged text.\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=True)}"
        )
        result = await self._generate_typed(
            prompt=prompt,
            schema_model=SafetyRewriteResponse,
            temperature=0.1,
        )
        return result.rewritten_text

    async def make_edge(self, beat_id: str, choice_id: str, event_ids: list[str]) -> TimelineEdge:
        return TimelineEdge(
            edge_id=stable_id("edge", f"{beat_id}:{choice_id}:{'-'.join(event_ids)}"),
            from_node=beat_id,
            to_node=f"{beat_id}:consequence:{choice_id}",
            edge_type="CHOICE_CAUSES",
            justification="Selected policy redirected institutional power.",
            supporting_event_ids=event_ids,
            confidence=0.84,
        )

    async def _generate_typed(
        self,
        *,
        prompt: str,
        schema_model: type[T],
        temperature: float,
    ) -> T:
        schema = schema_model.model_json_schema()

        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            try:
                return await asyncio.to_thread(
                    self._generate_once,
                    prompt=prompt,
                    schema=schema,
                    schema_model=schema_model,
                    temperature=temperature,
                )
            except (ClientError, RuntimeError, JSONDecodeError) as exc:
                if not self._should_retry(exc, attempt):
                    raise
                await asyncio.sleep(self.retry_base_delay_seconds * attempt)

        raise RuntimeError("unreachable retry exit in _generate_typed")

    def _generate_once(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        schema_model: type[T],
        temperature: float,
    ) -> T:
        response = self._next_client().models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
            ),
        )
        data = _extract_response_object(response)
        try:
            return schema_model.model_validate(data)
        except ValidationError as exc:  # pragma: no cover
            raise RuntimeError(f"invalid structured response: {exc}") from exc

    def _generate_interleaved_once(self, *, prompt: str, model_id: str) -> Any:
        return self._next_client().models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
            ),
        )

    def _next_client(self) -> genai.Client:
        with self._client_lock:
            client = self._clients[self._client_index % len(self._clients)]
            self._client_index += 1
            return client

    def _should_retry(self, exc: Exception, attempt: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        if isinstance(exc, ClientError):
            status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            return status_code in {429, 500, 502, 503, 504}
        if isinstance(exc, JSONDecodeError):
            return True
        if isinstance(exc, RuntimeError):
            return True
        return False


def _extract_response_object(response: Any) -> dict[str, Any]:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, BaseModel):
        payload = parsed.model_dump(mode="json")
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("structured parsed payload must be a JSON object")
    if isinstance(parsed, dict):
        return parsed
    if parsed is not None:
        raise RuntimeError("structured parsed payload must be a JSON object")

    text = response.text or ""
    if not text:
        raise RuntimeError("gemini response was empty")
    return _parse_json_block(text)


def _parse_json_block(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
    except JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise RuntimeError("structured response must be a JSON object")
    return parsed


def _extract_interleaved_parts(response: Any) -> tuple[str, list[InterleavedBlock]]:
    request_id_raw = (
        getattr(response, "response_id", None)
        or getattr(response, "request_id", None)
        or getattr(response, "id", None)
    )
    request_id = str(request_id_raw) if request_id_raw else stable_id("req", str(getattr(response, "text", "")))

    blocks: list[InterleavedBlock] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                stripped = text.strip()
                if stripped:
                    blocks.append(
                        InterleavedBlock(
                            part_order=len(blocks),
                            kind="text",
                            text=stripped,
                        )
                    )

            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None:
                mime_type = str(getattr(inline_data, "mime_type", "") or "")
                data = getattr(inline_data, "data", None)
                if mime_type.startswith("image/") and data is not None:
                    blocks.append(
                        InterleavedBlock(
                            part_order=len(blocks),
                            kind="image",
                            mime_type=mime_type,
                            inline_data_b64=_as_base64(data),
                        )
                    )

            file_data = getattr(part, "file_data", None)
            if file_data is not None:
                mime_type = str(getattr(file_data, "mime_type", "") or "")
                uri = getattr(file_data, "file_uri", None) or getattr(file_data, "uri", None)
                uri_is_image = isinstance(uri, str) and uri.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".webp", ".gif")
                )
                is_image_part = mime_type.startswith("image/") or (not mime_type and uri_is_image)
                if is_image_part and isinstance(uri, str) and uri:
                    blocks.append(
                        InterleavedBlock(
                            part_order=len(blocks),
                            kind="image",
                            mime_type=mime_type or "image/*",
                            uri=uri,
                        )
                    )

    if not blocks:
        text = str(getattr(response, "text", "") or "").strip()
        if text:
            blocks.append(
                InterleavedBlock(
                    part_order=0,
                    kind="text",
                    text=text,
                )
            )

    has_text = any(block.kind == "text" for block in blocks)
    has_image = any(block.kind == "image" for block in blocks)
    if not (has_text and has_image):
        raise RuntimeError("interleaved response must include both text and image parts")

    return request_id, blocks


def _as_base64(value: str | bytes) -> str:
    if isinstance(value, str):
        return value
    return base64.b64encode(value).decode("utf-8")


def create_agent_runtime_from_env() -> AgentRuntimeProtocol:
    api_key, additional_api_keys = _load_api_keys_from_env()
    model = os.getenv("WHATIF_GEMINI_MODEL") or os.getenv("WHATIF_VERTEX_MODEL", "gemini-2.5-flash")
    interleaved_model = os.getenv("WHATIF_INTERLEAVED_MODEL", "gemini-3.1-flash-image-preview")
    interleaved_fallback_model = os.getenv("WHATIF_INTERLEAVED_FALLBACK_MODEL") or "gemini-2.5-flash-image"
    max_attempts = int(os.getenv("WHATIF_AGENT_MAX_ATTEMPTS", "3"))
    retry_base_delay_seconds = float(os.getenv("WHATIF_AGENT_RETRY_BASE_DELAY_SECONDS", "0.8"))

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY must be set for Gemini agent runtime")

    return GeminiAgentRuntime(
        api_key=api_key,
        model=model,
        interleaved_model=interleaved_model,
        interleaved_fallback_model=interleaved_fallback_model,
        max_attempts=max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        additional_api_keys=additional_api_keys,
    )


def _load_api_keys_from_env() -> tuple[str | None, tuple[str, ...]]:
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
        return None, ()
    return keys[0], tuple(keys[1:])
