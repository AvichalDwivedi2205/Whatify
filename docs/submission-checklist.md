# Submission Checklist

Deadline: **March 16, 2026 at 5:00 PM PDT**

## Repo + Build
- [x] `services/orchestrator` tests pass (`uv run pytest`)
- [x] Web lint passes (`bun run lint:web`)
- [x] Console and cinematic UI builds pass

## Mandatory Creative Storyteller Evidence
- [x] Live Gemini session with interruption handling shown in runtime (`docs/proofs/live-ws-smoke-20260304T220023Z.json`)
- [x] Interleaved native mixed output visible (text + image blocks)
- [x] Proof endpoint captured in artifact (`docs/proofs/cloud-proof-latest.md`)

## Cloud Proof
- [x] Cloud Run orchestrator health endpoint clip (`/api/v1/system/health`)
- [x] Cloud Run workers health endpoint clip (`/api/v1/system/health`)
- [x] Session running against cloud URLs (not localhost)

## Demo Artifacts (< 4 min)
- [ ] Intro + cloud proof
- [ ] Live interruption
- [ ] Interleaved mixed output with metadata
- [ ] Explainability overlay
- [ ] Async media transition

## Package
- [x] Architecture diagram included (`docs/architecture-diagram.md`)
- [x] Demo script included (`docs/demo-script-4min.md`)
- [x] README includes spin-up + proof curl commands
