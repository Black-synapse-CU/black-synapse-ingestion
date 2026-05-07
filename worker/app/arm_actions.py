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
_REST_BASE         =  0.0
_REST_SHOULDER     = -0.5
_REST_ELBOW        = -0.8
_REST_WRIST_PITCH  =  0.0
_REST_WRIST_ROLL   =  0.0
_REST_GRIPPER      =  0.5   # half-open

# Neutral / home (arm horizontal, ready to work)
_NEUTRAL_BASE         =  0.0
_NEUTRAL_SHOULDER     =  0.0
_NEUTRAL_ELBOW        =  0.0
_NEUTRAL_WRIST_PITCH  =  0.0
_NEUTRAL_WRIST_ROLL   =  0.0
_NEUTRAL_GRIPPER      =  0.5

# Fully extended (arm reaching forward)
_EXTEND_BASE         =  0.0
_EXTEND_SHOULDER     =  0.3
_EXTEND_ELBOW        =  0.7
_EXTEND_WRIST_PITCH  =  0.0
_EXTEND_WRIST_ROLL   =  0.0
_EXTEND_GRIPPER      =  1.0   # open

# Retracted (arm pulled close to body)
_RETRACT_BASE         =  0.0
_RETRACT_SHOULDER     = -0.3
_RETRACT_ELBOW        = -0.6
_RETRACT_WRIST_PITCH  =  0.0
_RETRACT_WRIST_ROLL   =  0.0
_RETRACT_GRIPPER      = -1.0  # closed

# Point (shoulder up, elbow partial)
_POINT_BASE         =  0.0
_POINT_SHOULDER     =  0.6
_POINT_ELBOW        =  0.3
_POINT_WRIST_PITCH  =  0.0
_POINT_WRIST_ROLL   =  0.0
_POINT_GRIPPER      = -0.8  # mostly closed / pointing


_SLOW_STEPS = 50   # interpolation points per keyframe — higher = smoother


def _pose(orch: ServoOrchestrator,
          base: float, shoulder: float, elbow: float,
          wrist_pitch: float, wrist_roll: float, gripper: float) -> Pose:
    """Build a Pose from normalised values using the current layout."""
    L = orch.layout
    return Pose(
        base        = normalized_to_us(L.base,        base),
        shoulder    = normalized_to_us(L.shoulder_a,  shoulder),
        elbow       = normalized_to_us(L.elbow,       elbow),
        wrist_pitch = normalized_to_us(L.wrist_pitch, wrist_pitch),
        wrist_roll  = normalized_to_us(L.wrist_roll,  wrist_roll),
        gripper     = normalized_to_us(L.gripper,     gripper),
    )


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _wave_sequence(orch: ServoOrchestrator) -> Sequence:
    def p(sh: float, el: float, wr: float = 0.0) -> Pose:
        return _pose(orch, 0.0, sh, el, 0.0, wr, 0.5)

    return (
        Sequence()
        .add(p(0.5, 0.3),          duration_s=2.0, steps=_SLOW_STEPS)  # raise arm
        .add(p(0.5, 0.7,  0.5),    duration_s=1.5, steps=_SLOW_STEPS)  # wave out
        .add(p(0.5, 0.1, -0.5),    duration_s=1.5, steps=_SLOW_STEPS)  # wave in
        .add(p(0.5, 0.7,  0.5),    duration_s=1.5, steps=_SLOW_STEPS)  # wave out
        .add(p(0.5, 0.1, -0.5),    duration_s=1.5, steps=_SLOW_STEPS)  # wave in
        .add(p(0.0, 0.0),          duration_s=2.0, steps=_SLOW_STEPS)  # return neutral
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
            _pose(orch, _REST_BASE, _REST_SHOULDER, _REST_ELBOW,
                  _REST_WRIST_PITCH, _REST_WRIST_ROLL, _REST_GRIPPER),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return "Arm moved to rest position."

    elif action == "neutral":
        Sequence().add(
            _pose(orch, _NEUTRAL_BASE, _NEUTRAL_SHOULDER, _NEUTRAL_ELBOW,
                  _NEUTRAL_WRIST_PITCH, _NEUTRAL_WRIST_ROLL, _NEUTRAL_GRIPPER),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return "Arm moved to neutral / home position."

    elif action == "extend":
        pct = amount if amount is not None else 100
        t = max(0.0, min(1.0, pct / 100.0))
        Sequence().add(
            _pose(orch,
                  _EXTEND_BASE,
                  _lerp(0.0, _EXTEND_SHOULDER,    t),
                  _lerp(0.0, _EXTEND_ELBOW,       t),
                  _lerp(0.0, _EXTEND_WRIST_PITCH, t),
                  _EXTEND_WRIST_ROLL,
                  _EXTEND_GRIPPER),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return f"Extended arm to {pct}%."

    elif action == "retract":
        pct = amount if amount is not None else 100
        t = max(0.0, min(1.0, pct / 100.0))
        Sequence().add(
            _pose(orch,
                  _RETRACT_BASE,
                  _lerp(0.0, _RETRACT_SHOULDER,    t),
                  _lerp(0.0, _RETRACT_ELBOW,       t),
                  _lerp(0.0, _RETRACT_WRIST_PITCH, t),
                  _RETRACT_WRIST_ROLL,
                  _RETRACT_GRIPPER),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return f"Retracted arm to {pct}%."

    elif action == "point":
        Sequence().add(
            _pose(orch, _POINT_BASE, _POINT_SHOULDER, _POINT_ELBOW,
                  _POINT_WRIST_PITCH, _POINT_WRIST_ROLL, _POINT_GRIPPER),
            duration_s=2.5, steps=_SLOW_STEPS,
        ).play(orch)
        return "Pointing."

    elif action == "wave":
        _wave_sequence(orch).play(orch)
        return "Waved."

    elif action == "grab":
        L = orch.layout
        Sequence().add(
            Pose(gripper=normalized_to_us(L.gripper, -1.0)),
            duration_s=1.0, steps=_SLOW_STEPS,
        ).play(orch)
        return "Gripper closed."

    elif action == "release_grip":
        L = orch.layout
        Sequence().add(
            Pose(gripper=normalized_to_us(L.gripper, 1.0)),
            duration_s=1.0, steps=_SLOW_STEPS,
        ).play(orch)
        return "Gripper opened."

    elif action == "release":
        orch.arm.release()
        return "Arm torque released. Arm can be moved by hand."

    else:
        raise ValueError(
            f"Unknown action '{action}'. "
            "Valid: rest, neutral, extend, retract, point, wave, grab, release_grip, release."
        )
