from __future__ import annotations

import pytest

from app.core.state_machine import InvalidTransition, TransitionCommand, apply_transition
from app.models.enums import Mode


def test_illegal_transition_from_choice_to_ready_for_choices() -> None:
    with pytest.raises(InvalidTransition):
        apply_transition(Mode.CHOICE, TransitionCommand.READY_FOR_CHOICES)
