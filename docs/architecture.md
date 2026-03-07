# WhatIf Architecture (Current)

## Control Plane

- `services/orchestrator` is a deterministic state machine (`STORY`, `CHOICE`, `EXPLAIN`, `NAV`).
- All user-visible effects are emitted as typed `UIAction` records with ACK + retry support.
- Every state mutation appends an event record for replayability and post-hoc explainability.

## Agent Plane (Vertex)

Orchestrator invokes specialized structured-output agents:

- Story Planner -> `BeatSpec`
- Canon & Consistency -> `ConsistencyReport`
- Shot Planner -> `ShotPlan`
- Interleaved Story Generator -> ordered mixed `text/image` parts from one Gemini response
- Explainer -> `ExplainResponse`
- Historian -> `RealityResponse`
- Safety Editor -> rewritten safe text

All agent responses are schema-validated before acceptance.

## Live Voice Plane (Track 2)

- ADK BIDI live runtime (`Runner.run_live`) is integrated behind:
  - `WS /api/v1/session/{session_id}/live`
- Live runtime uses:
  - `response_modalities=["AUDIO"]`
  - input/output transcription enabled
  - optional affective dialog + proactive audio flags
- Voice Director exposes tool-based control signals (`INTERRUPT`, `READY_FOR_CHOICES`, `PACE_HINT`, `EMOTION_TARGET`) that route back into orchestrator state transitions.

## Data Plane

- Redis: hot session state + working context + action sequencing
- Firestore: event log, beat specs, beat summaries, timeline edges, asset registry
- Pub/Sub: async asset job transport
- GCS: storyboard and hero-video outputs

## Media Plane

1. Orchestrator emits placeholder storyboard actions immediately.
2. Orchestrator schedules interleaved generation and emits `SHOW_INTERLEAVED_BLOCKS` actions in-order.
3. Shot plan emits storyboard jobs and selective hero video jobs.
4. Workers consume Pub/Sub push jobs at `/jobs/pubsub`.
5. Workers generate assets (Imagen/Veo) and callback orchestrator.
6. Orchestrator swaps storyboard/video via typed actions (`SHOW_STORYBOARD`, `PLAY_VIDEO`).

## Explainability Path

- Choice selection writes `CHOICE_SELECTED` event.
- Causal edge is persisted in timeline graph store.
- `WHY/REWIND/COMPARE` interruptions query timeline edges + support events.
- `COMPARE` responses are citation-grounded through historian source retrieval before UI emission.
- Explainer answer is grounded in stored chain and emitted with overlay payload.
