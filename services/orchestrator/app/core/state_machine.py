from __future__ import annotations

from enum import StrEnum

from app.models.enums import Mode


class InvalidTransition(ValueError):
    pass


class TransitionCommand(StrEnum):
    BEGIN_SESSION = "BEGIN_SESSION"
    CONTINUE_STORY = "CONTINUE_STORY"
    READY_FOR_CHOICES = "READY_FOR_CHOICES"
    USER_CHOICE = "USER_CHOICE"
    ENTER_EXPLAIN = "ENTER_EXPLAIN"
    ENTER_INTERMISSION = "ENTER_INTERMISSION"
    RESUME_STORY = "RESUME_STORY"
    MARK_COMPLETE = "MARK_COMPLETE"
    NAVIGATE = "NAVIGATE"


_ALLOWED_TRANSITIONS: dict[Mode, dict[TransitionCommand, Mode]] = {
    Mode.ONBOARDING: {
        TransitionCommand.BEGIN_SESSION: Mode.STORY,
        TransitionCommand.NAVIGATE: Mode.NAV,
    },
    Mode.STORY: {
        TransitionCommand.CONTINUE_STORY: Mode.STORY,
        TransitionCommand.READY_FOR_CHOICES: Mode.CHOICE,
        TransitionCommand.ENTER_EXPLAIN: Mode.EXPLAIN,
        TransitionCommand.ENTER_INTERMISSION: Mode.INTERMISSION,
        TransitionCommand.MARK_COMPLETE: Mode.COMPLETE,
        TransitionCommand.NAVIGATE: Mode.NAV,
    },
    Mode.CHOICE: {
        TransitionCommand.USER_CHOICE: Mode.INTERMISSION,
        TransitionCommand.ENTER_EXPLAIN: Mode.EXPLAIN,
        TransitionCommand.MARK_COMPLETE: Mode.COMPLETE,
        TransitionCommand.NAVIGATE: Mode.NAV,
    },
    Mode.EXPLAIN: {
        TransitionCommand.RESUME_STORY: Mode.STORY,
        TransitionCommand.READY_FOR_CHOICES: Mode.CHOICE,
        TransitionCommand.ENTER_INTERMISSION: Mode.INTERMISSION,
        TransitionCommand.MARK_COMPLETE: Mode.COMPLETE,
        TransitionCommand.NAVIGATE: Mode.NAV,
    },
    Mode.INTERMISSION: {
        TransitionCommand.CONTINUE_STORY: Mode.STORY,
        TransitionCommand.ENTER_EXPLAIN: Mode.EXPLAIN,
        TransitionCommand.MARK_COMPLETE: Mode.COMPLETE,
        TransitionCommand.NAVIGATE: Mode.NAV,
    },
    Mode.COMPLETE: {
        TransitionCommand.ENTER_EXPLAIN: Mode.EXPLAIN,
        TransitionCommand.NAVIGATE: Mode.NAV,
    },
    Mode.NAV: {
        TransitionCommand.CONTINUE_STORY: Mode.STORY,
    },
}


def apply_transition(current_mode: Mode, command: TransitionCommand) -> Mode:
    transitions = _ALLOWED_TRANSITIONS.get(current_mode, {})
    if command not in transitions:
        raise InvalidTransition(f"invalid transition: {current_mode} -> {command}")
    return transitions[command]
