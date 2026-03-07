# ADK / Vertex Runtime Notes

The orchestrator now runs real Vertex-backed agent roles by default.

Implemented in `services/orchestrator/app/core/agent_runtime.py`:

- Story Planner -> `BeatSpec`
- Canon & Consistency -> `ConsistencyReport`
- Shot Planner -> `ShotPlan`
- Explainer -> `ExplainResponse`
- Historian -> `RealityResponse`
- Safety Editor -> `SafetyRewriteResponse`

Key constraints:

1. Every role call requests JSON with strict schema validation.
2. Invalid model outputs raise runtime errors; no auto-generated mock fallback path in production runtime.
3. Runtime requires valid Vertex credentials and env vars before startup.

Required env:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `WHATIF_VERTEX_MODEL`
