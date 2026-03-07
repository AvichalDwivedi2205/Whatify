from __future__ import annotations

from app.core.schema_gatekeeper import validate_beat_spec


def test_beat_spec_schema_rejects_single_choice() -> None:
    payload = {
        "beat_id": "beat_1",
        "objective": "objective",
        "setup": "setup",
        "escalation": "escalation",
        "choices": [{"choice_id": "c1", "label": "only", "consequence_hint": "bad"}],
        "consequence_seed": "seed",
        "transition_hook": "hook",
        "active_entities": [],
        "branch_rule_updates": [],
    }
    assert validate_beat_spec(payload) is None
