# Cloud Proof (20260304T184651Z)

- ORCH_URL: https://whatif-orchestrator-vgs7ulm4xa-uc.a.run.app
- WORKER_URL: https://whatif-workers-vgs7ulm4xa-uc.a.run.app
- SESSION_ID: s_9de692a4aa1b504a
- BEAT_ID: beat_1

## Health
- Orchestrator: `GET /api/v1/system/health` -> {"service":"whatif-orchestrator","status":"ok"}
- Workers: `GET /api/v1/system/health` -> {"service":"whatif-workers","status":"ok"}

## Session Start
- Request payload: {"divergence_point":"What if the Library of Alexandria never burned?","tone":"cinematic","pacing":"normal"}
- Response saved: `docs/proofs/20260304T184651Z/start-session.json`

## Interleaved Proof (Beat Start)
- Endpoint: `GET /api/v1/session/s_9de692a4aa1b504a/interleaved-proof?beat_id=beat_1`
- run_id: `ilv_7d2667558241567b`
- trigger: `BEAT_START`
- model_id: `gemini-2.5-flash-image`
- request_id: `WX6oaeqbCsyT1dkPg_G94Aw`
- text_blocks: 2
- image_blocks: 2
- first_image_uri: `inline://redacted/ilv_7d2667558241567b/1`
- Raw JSON: `docs/proofs/20260304T184651Z/interleaved-beat-start.json`

## Interleaved Proof (COMPARE Trigger)
- Interrupt request saved: `docs/proofs/20260304T184651Z/compare-interrupt.json`
- run_id: `ilv_ca714f6b78b75767`
- trigger: `COMPARE`
- model_id: `gemini-2.5-flash-image`
- request_id: `eX6oadHFEo73nvgPxZ2KoAI`
- text_blocks: 3
- image_blocks: 2
- first_image_uri: `inline://redacted/ilv_ca714f6b78b75767/1`
- Raw JSON: `docs/proofs/20260304T184651Z/interleaved-compare.json`

## Session Evidence
- State JSON: `docs/proofs/20260304T184651Z/state.json`
- Timeline JSON: `docs/proofs/20260304T184651Z/timeline.json`

## Live WebSocket Smoke
- Session: `s_c191150e4f4e549d`
- Endpoint: `wss://whatif-orchestrator-vgs7ulm4xa-uc.a.run.app/api/v1/session/s_c191150e4f4e549d/live?user_id=live-proof-refresh`
- Observed message types: `adk_event`, `output_transcript`, `audio_chunk`
- Live model config: `gemini-live-2.5-flash-native-audio`
- Raw JSON: `docs/proofs/live-ws-smoke-20260304T220023Z.json`
