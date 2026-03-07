# WhatIf 4-Minute Demo Script (Hackathon)

## 0:00 - 0:30 Intro + Cloud Proof
- Show deployed orchestrator `/api/v1/system/health` on Cloud Run.
- Show cinematic UI connected to cloud endpoint.
- State challenge category: Creative Storyteller + Live agent.

## 0:30 - 1:25 Live Scene Start
- Start session from cinematic UI.
- Connect Gemini Live and begin narration (mic/text).
- Highlight real-time captions + audio with no local mock.

## 1:25 - 2:10 Interleaved Native Mixed Output (Mandatory)
- Show `SHOW_INTERLEAVED_BLOCKS` rendering text/image blocks in sequence.
- Show this came from one model response path (`gemini-2.5-flash-image`).
- Open proof endpoint:
  - `/api/v1/session/<id>/interleaved-proof?beat_id=beat_1`
- Point to `model_id`, `request_id`, ordered `part_order`, and mixed block types.

## 2:10 - 2:55 Interruption + Explainability
- Trigger `COMPARE` interruption during/after narration.
- Show overlay pause behavior and comparison cards.
- Show timeline rail causal edges + confidence.
- Mention persisted `COMPARE_ANSWERED` event with citation metadata.

## 2:55 - 3:35 Async Media Swap + Recovery
- Choose a branch; move to next beat.
- Show storyboard placeholders -> ready frames.
- Show hero video swap without resetting session context.

## 3:35 - 4:00 Reliability + Close
- Mention action ACK + retry loop, callback idempotency, and schema-typed actions.
- Mention automated compliance tests for interleaved beat-start + compare trigger paths.
- Close with production architecture slide.
