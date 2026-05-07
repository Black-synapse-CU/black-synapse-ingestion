"""Keyframe sequence playback across all servo joints."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from action_servos.groups import ServoOrchestrator, clamp_pulse, normalized_to_us


@dataclass
class Pose:
    """
    Target state for one keyframe.  All fields are pulse µs; None = don't move that joint.

    Arm joints (6 DOF):
        base, shoulder, elbow, wrist_pitch, wrist_roll, gripper
    Head / ear (optional):
        head_tilt, head_pan, ear
    """

    base:        Optional[float] = None
    shoulder:    Optional[float] = None
    elbow:       Optional[float] = None
    wrist_pitch: Optional[float] = None
    wrist_roll:  Optional[float] = None
    gripper:     Optional[float] = None
    head_tilt:   Optional[float] = None
    head_pan:    Optional[float] = None
    ear:         Optional[float] = None

    @classmethod
    def from_normalized(
        cls,
        orch: ServoOrchestrator,
        base:        Optional[float] = None,
        shoulder:    Optional[float] = None,
        elbow:       Optional[float] = None,
        wrist_pitch: Optional[float] = None,
        wrist_roll:  Optional[float] = None,
        gripper:     Optional[float] = None,
        head_tilt:   Optional[float] = None,
        head_pan:    Optional[float] = None,
        ear:         Optional[float] = None,
    ) -> Pose:
        """Convenience constructor accepting normalised [-1, 1] values."""
        L = orch.layout
        return cls(
            base        = normalized_to_us(L.base,        base)        if base        is not None else None,
            shoulder    = normalized_to_us(L.shoulder_a,  shoulder)    if shoulder    is not None else None,
            elbow       = normalized_to_us(L.elbow,       elbow)       if elbow       is not None else None,
            wrist_pitch = normalized_to_us(L.wrist_pitch, wrist_pitch) if wrist_pitch is not None else None,
            wrist_roll  = normalized_to_us(L.wrist_roll,  wrist_roll)  if wrist_roll  is not None else None,
            gripper     = normalized_to_us(L.gripper,     gripper)     if gripper     is not None else None,
            head_tilt   = normalized_to_us(L.head_tilt,   head_tilt)   if (head_tilt  is not None and L.head_tilt  is not None) else None,
            head_pan    = normalized_to_us(L.head_pan,    head_pan)    if (head_pan   is not None and L.head_pan   is not None) else None,
            ear         = normalized_to_us(L.ear,         ear)         if (ear        is not None and L.ear        is not None) else None,
        )


@dataclass
class Keyframe:
    """A target Pose plus the ramp duration to reach it from the previous keyframe."""

    pose: Pose
    duration_s: float = 0.5
    steps: int = 20


@dataclass
class Sequence:
    """Ordered list of keyframes; play() executes them blocking in order."""

    keyframes: List[Keyframe] = field(default_factory=list)

    def add(self, pose: Pose, duration_s: float = 0.5, steps: int = 20) -> Sequence:
        """Append a keyframe; returns self for chaining."""
        self.keyframes.append(Keyframe(pose=pose, duration_s=duration_s, steps=steps))
        return self

    def play(self, orch: ServoOrchestrator) -> None:
        """Execute all keyframes in order. Blocking."""
        for kf in self.keyframes:
            _ramp_to_pose(orch, kf.pose, kf.duration_s, kf.steps)


def _ramp_to_pose(
    orch: ServoOrchestrator,
    pose: Pose,
    duration_s: float,
    steps: int,
) -> None:
    """Ramp all specified joints simultaneously using smoothstep easing."""
    L = orch.layout
    s = orch.arm.last_state

    # Current arm positions (fallback to centre)
    cs_b  = s.base        if s.base        is not None else L.base.center_us
    cs_sh = s.shoulder    if s.shoulder    is not None else L.shoulder_a.center_us
    cs_el = s.elbow       if s.elbow       is not None else L.elbow.center_us
    cs_wp = s.wrist_pitch if s.wrist_pitch is not None else L.wrist_pitch.center_us
    cs_wr = s.wrist_roll  if s.wrist_roll  is not None else L.wrist_roll.center_us
    cs_gr = s.gripper     if s.gripper     is not None else L.gripper.center_us

    # Targets (pose fields override current; None means stay)
    t_b  = clamp_pulse(L.base,        pose.base)        if pose.base        is not None else cs_b
    t_sh = clamp_pulse(L.shoulder_a,  pose.shoulder)    if pose.shoulder    is not None else cs_sh
    t_el = clamp_pulse(L.elbow,       pose.elbow)       if pose.elbow       is not None else cs_el
    t_wp = clamp_pulse(L.wrist_pitch, pose.wrist_pitch) if pose.wrist_pitch is not None else cs_wp
    t_wr = clamp_pulse(L.wrist_roll,  pose.wrist_roll)  if pose.wrist_roll  is not None else cs_wr
    t_gr = clamp_pulse(L.gripper,     pose.gripper)     if pose.gripper     is not None else cs_gr

    move_arm = any(f is not None for f in (
        pose.base, pose.shoulder, pose.elbow,
        pose.wrist_pitch, pose.wrist_roll, pose.gripper,
    ))

    # Head / ear targets
    move_head = pose.head_tilt is not None or pose.head_pan is not None
    move_ear  = pose.ear is not None
    head_lp = orch._head_ctl.last_pulses if orch._head_ctl is not None else (None, None)
    ear_lp  = orch._ear_ctl.last_pulse   if orch._ear_ctl  is not None else None

    s_pan  = head_lp[0] if head_lp[0] is not None else (L.head_pan.center_us  if L.head_pan  is not None else 0.0)
    s_tilt = head_lp[1] if head_lp[1] is not None else (L.head_tilt.center_us if L.head_tilt is not None else 0.0)
    s_ear  = ear_lp     if ear_lp     is not None else (L.ear.center_us        if L.ear       is not None else 0.0)

    t_tilt = clamp_pulse(L.head_tilt, pose.head_tilt) if (pose.head_tilt is not None and L.head_tilt is not None) else s_tilt
    t_pan  = clamp_pulse(L.head_pan,  pose.head_pan)  if (pose.head_pan  is not None and L.head_pan  is not None) else s_pan
    t_ear  = clamp_pulse(L.ear,       pose.ear)        if (pose.ear       is not None and L.ear       is not None) else s_ear

    duration_s = max(0.01, float(duration_s))
    steps = max(2, int(steps))

    for i in range(1, steps + 1):
        t = i / float(steps)
        a = t * t * (3.0 - 2.0 * t)   # smoothstep: ease-in/out

        if move_arm:
            orch.arm.set_all(
                cs_b  + (t_b  - cs_b)  * a,
                cs_sh + (t_sh - cs_sh) * a,
                cs_el + (t_el - cs_el) * a,
                cs_wp + (t_wp - cs_wp) * a,
                cs_wr + (t_wr - cs_wr) * a,
                cs_gr + (t_gr - cs_gr) * a,
            )
        if move_head and orch._head_ctl is not None:
            orch._head_ctl.set_pulses(
                s_pan  + (t_pan  - s_pan)  * a,
                s_tilt + (t_tilt - s_tilt) * a,
            )
        if move_ear and orch._ear_ctl is not None:
            orch._ear_ctl.set_pulse(s_ear + (t_ear - s_ear) * a)

        time.sleep(duration_s / steps)
