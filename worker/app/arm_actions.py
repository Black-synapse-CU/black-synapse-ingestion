"""Pre-configured arm actions for the 6-DOF arm. No raw servo values leak out."""

from __future__ import annotations

from action_servos.groups import ServoOrchestrator, normalized_to_us
from action_servos.sequences import Pose, Sequence

# ---------------------------------------------------------------------------
# Calibration constants — tune these for the physical robot.
# All values are normalised (-1..1): -1 = min pulse, 0 = centre, +1 = max pulse.
# Axes:
#   base        — pan rotation  (-1 = full left, +1 = full right)
#   shoulder    — lift          (-1 = down,       +1 = up)
#   elbow       — forearm lift  (-1 = folded,     +1 = extended)
#   wrist_pitch — wrist up/down (-1 = down,       +1 = up)
#   wrist_roll  — wrist rotate  (-1 = CCW,        +1 = CW)
#   gripper     — open/close    (-1 = closed,     +1 = open)
# ---------------------------------------------------------------------------

# Rest (arm folded down, out of the way)
_REST_BASE     =  0.0
_REST_SHOULDER = -0.5
_REST_ELBOW    = -0.8

# Neutral / home (arm horizontal, ready to work)
_NEUTRAL_BASE     = 0.0
_NEUTRAL_SHOULDER = 0.0
_NEUTRAL_ELBOW    = 0.0

# Fully extended (arm reaching forward)
_EXTEND_BASE     = 0.0
_EXTEND_SHOULDER = 0.3
_EXTEND_ELBOW    = 0.7

# Retracted (arm pulled close to body)
_RETRACT_BASE     =  0.0
_RETRACT_SHOULDER = -0.3
_RETRACT_ELBOW    = -0.6

# Point (shoulder up, elbow partial)
_POINT_BASE     = 0.0
_POINT_SHOULDER = 0.6
_POINT_ELBOW    = 0.3


_SLOW_STEPS = 50   # interpolation points per keyframe — higher = smoother


def _pose(orch: ServoOrchestrator, base: float, shoulder: float, elbow: float) -> Pose:
    """Build a Pose from normalised values using the current layout."""
    L = orch.layout
    return Pose(
        base     = normalized_to_us(L.base,       base),
        shoulder = normalized_to_us(L.shoulder_a, shoulder),
        elbow    = normalized_to_us(L.elbow,      elbow),
    )


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _wave_sequence(orch: ServoOrchestrator) -> Sequence:
    def p(sh: float, el: float) -> Pose:
        return _pose(orch, 0.0, sh, el)

    return (
        Sequence()
        .add(p(0.7, 0.3),  duration_s=2.0, steps=_SLOW_STEPS)  # raise arm
        .add(p(0.7, 0.7),  duration_s=1.5, steps=_SLOW_STEPS)  # wave out
        .add(p(0.7, 0.1),  duration_s=1.5, steps=_SLOW_STEPS)  # wave in
        .add(p(0.7, 0.7),  duration_s=1.5, steps=_SLOW_STEPS)  # wave out
        .add(p(0.7, 0.1),  duration_s=1.5, steps=_SLOW_STEPS)  # wave in
        .add(p(0.0, 0.0),  duration_s=2.0, steps=_SLOW_STEPS)  # return neutral
    )


def execute_action(orch: ServoOrchestrator, action: str, amount: int | None) -> str:
    """
    Dispatch a named action to the servo orchestrator.

    Returns a human-readable description of what happened.
    Raises ValueError for unknown actions.
    """
    action = action.strip().lower()

    if action == "rest":
        Sequence().add(
            _pose(orch, _REST_BASE, _REST_SHOULDER, _REST_ELBOW),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return "Arm moved to rest position."

    elif action == "neutral":
        Sequence().add(
            _pose(orch, _NEUTRAL_BASE, _NEUTRAL_SHOULDER, _NEUTRAL_ELBOW),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return "Arm moved to neutral / home position."

    elif action == "extend":
        pct = amount if amount is not None else 30
        t = max(0.0, min(1.0, pct / 100.0))
        Sequence().add(
            _pose(orch,
                  _EXTEND_BASE,
                  _lerp(0.0, _EXTEND_SHOULDER, t),
                  _lerp(0.0, _EXTEND_ELBOW,    t)),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return f"Extended arm to {pct}%."

    elif action == "retract":
        pct = amount if amount is not None else 30
        t = max(0.0, min(1.0, pct / 100.0))
        Sequence().add(
            _pose(orch,
                  _RETRACT_BASE,
                  _lerp(0.0, _RETRACT_SHOULDER, t),
                  _lerp(0.0, _RETRACT_ELBOW,    t)),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return f"Retracted arm to {pct}%."

    elif action == "point":
        Sequence().add(
            _pose(orch, _POINT_BASE, _POINT_SHOULDER, _POINT_ELBOW),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return "Pointing."

    elif action == "wave":
        _wave_sequence(orch).play(orch)
        return "Waved."

    elif action == "release":
        orch.arm.release()
        return "Arm torque released. Arm can be moved by hand."

    else:
        raise ValueError(
            f"Unknown action '{action}'. "
            "Valid: rest, neutral, extend, retract, point, wave, release."
        )
