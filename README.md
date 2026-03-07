# WhatIf

Voice-first cinematic alternate-history engine with deterministic orchestration, strict typed agent contracts, event-sourced memory, and async storyboard/video generation.

## Stack

- Backend: Python 3.11+, FastAPI, Vertex AI (Gemini Live / Gemini / Imagen / Veo), ADK live runtime
- Data plane: Upstash Redis (hot state), Firestore (events/summaries/graph/assets), Pub/Sub (asset jobs), GCS (asset files)
- Tooling: `uv` for Python, `bun` for web workspaces
- Frontend: Next.js 15+, React 19, Tailwind

## Repo Layout

- `services/orchestrator`: deterministic control plane, APIs, action/caption/live streams
- `services/workers`: async storyboard/video workers with callback writes
- `services/agents`: ADK role module shell and docs
- `apps/console`: live testing console (text + mic + audio playback)
- `apps/cinematic-ui`: cinematic desktop-first frontend shell
- `schemas/json`: strict JSON contracts

## Required Environment

Copy `.env.example` to `.env` and set values.

Orchestrator:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `WHATIF_VERTEX_LOCATION` (recommended `global`)
- `UPSTASH_REDIS_REST_URL`
- `UPSTASH_REDIS_REST_TOKEN`
- `WHATIF_PUBSUB_TOPIC`
- `WHATIF_ORCHESTRATOR_CALLBACK_URL`
- `WHATIF_VERTEX_MODEL` (recommended `gemini-3.1-flash-lite-preview`)
- `WHATIF_INTERLEAVED_MODEL` (recommended `gemini-3.1-flash-image-preview`)
- `WHATIF_INTERLEAVED_FALLBACK_MODEL` (recommended `gemini-2.5-flash-image`)
- `WHATIF_AGENT_MAX_ATTEMPTS` (recommended `3`)
- `WHATIF_AGENT_RETRY_BASE_DELAY_SECONDS` (recommended `0.8`)
- `WHATIF_LIVE_LOCATION` (recommended `us-central1`)
- `WHATIF_LIVE_MODEL` (recommended `gemini-live-2.5-flash-native-audio`)
- `WHATIF_LIVE_VOICE`
- `GOOGLE_GENAI_USE_VERTEXAI=true`

Workers:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `WHATIF_ASSET_BUCKET`
- `WHATIF_IMAGE_MODEL`
- `WHATIF_VIDEO_MODEL`

## Cloud Deployment (Real Infra)

Use [docs/deploy-cloud-run.md](/Users/avichaldwivedi/dev/Whatify/docs/deploy-cloud-run.md).

Current target in this project:

- GCP project: `brio-488311`
- region: `us-central1`
- Pub/Sub topic: `whatif-asset-jobs`
- worker subscription: `whatif-asset-jobs-workers`

Submission docs:

- [Architecture diagram](/Users/avichaldwivedi/dev/Whatify/docs/architecture-diagram.md)
- [4-minute demo script](/Users/avichaldwivedi/dev/Whatify/docs/demo-script-4min.md)
- [Submission checklist](/Users/avichaldwivedi/dev/Whatify/docs/submission-checklist.md)
- [Latest cloud proof artifact](/Users/avichaldwivedi/dev/Whatify/docs/proofs/cloud-proof-latest.md)

## Local Console Against Cloud

1. Install dependencies:

```bash
cd /Users/avichaldwivedi/dev/Whatify
bun install
```

2. Set console target:

```bash
cat > apps/console/.env.local <<'ENV'
NEXT_PUBLIC_ORCHESTRATOR_URL=https://<your-orchestrator-service-url>
ENV
```

3. Run console:

```bash
cd /Users/avichaldwivedi/dev/Whatify
bun run dev:console
```

Open [http://localhost:3001](http://localhost:3001)

## API

- `POST /api/v1/session/start`
- `POST /api/v1/session/{session_id}/begin`
- `POST /api/v1/session/{session_id}/continue`
- `GET /api/v1/system/health`
- `POST /api/v1/session/{session_id}/interrupt`
- `POST /api/v1/session/{session_id}/asset-explain`
- `POST /api/v1/session/{session_id}/choice`
- `POST /api/v1/session/{session_id}/ack`
- `GET /api/v1/session/{session_id}/state`
- `GET /api/v1/session/{session_id}/timeline`
- `GET /api/v1/session/{session_id}/interleaved-proof?beat_id=beat_2`
- `POST /api/v1/assets/jobs/callback`
- `WS /api/v1/session/{session_id}/actions`
- `WS /api/v1/session/{session_id}/captions`
- `WS /api/v1/session/{session_id}/live`

## Live WebSocket Protocol

Client -> server:

- Binary frame: raw PCM16 mono audio chunk (default `audio/pcm;rate=16000`)
- JSON `{ "type": "text", "text": "..." }`
- JSON `{ "type": "scene_snapshot", "snapshot": { ... } }`
- JSON `{ "type": "activity_start" }`
- JSON `{ "type": "activity_end" }`

Server -> client:

- `{ "type": "adk_event", "event": {...} }`
- `{ "type": "input_transcript", "text": "...", "final": true|false }`
- `{ "type": "output_transcript", "text": "...", "final": true|false }`
- `{ "type": "audio_chunk", "mime_type": "audio/pcm;rate=24000", "data": "<base64>" }`

Interleaved proof check:

```bash
curl "$ORCH_URL/api/v1/system/health"
curl "$ORCH_URL/api/v1/session/<SESSION_ID>/interleaved-proof?beat_id=beat_1"
```

## Tests

```bash
cd /Users/avichaldwivedi/dev/Whatify/services/orchestrator
uv run pytest
```
