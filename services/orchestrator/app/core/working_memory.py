from __future__ import annotations

from typing import Any

from app.storage.repository import RepositoryProtocol


async def build_working_memory(repo: RepositoryProtocol, session_id: str) -> dict[str, Any]:
    state = await repo.get_session(session_id)
    context = await repo.get_session_context(session_id)
    summaries = await repo.recent_beat_summaries(session_id, limit=2)
    return {
        "current_beat_objective": f"Beat {state.beat_index} progression",
        "active_entities": state.active_entities[:5],
        "branch_rules": context.get("branch_rules", [])[:5],
        "last_beat_summaries": [summary.summary_5_lines for summary in summaries],
        "tone": state.user_tone,
        "divergence_point": context.get("divergence_point", ""),
    }
