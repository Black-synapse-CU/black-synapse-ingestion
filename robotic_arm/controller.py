"""High-level robotic arm controller over the Arduino serial bridge."""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from robotic_arm.config import ArmLayout, JointSpec
from robotic_arm.serial_bridge import SerialBridge

logger = logging.getLogger(__name__)


class RoboticArmController:
    """
    Controls a 6-DOF arm through a SerialBridge connected to an Arduino.

    Joints: base, shoulder, elbow, wrist_rot, wrist_flex, gripper.
    Positions are tracked in pulse µs internally; callers can use normalized [-1,1].
    """

    def __init__(self, bridge: SerialBridge, layout: Optional[ArmLayout] = None) -> None:
        self._bridge = bridge
        self.layout = layout or ArmLayout()
        # Track last sent pulse per channel (µs).
        self._state: Dict[int, float] = {}

    # ------------------------------------------------------------------
    # Low-level

    def set_joint_us(self, name: str, pulse_us: float) -> None:
        """Move a named joint to an absolute pulse width (µs)."""
        spec = self._get_spec(name)
        clamped = spec.clamp(pulse_us)
        self._bridge.set_channel(spec.channel, clamped)
        self._state[spec.channel] = clamped
        logger.debug("joint=%s ch=%d pulse_us=%.1f", name, spec.channel, clamped)

    def set_joint(self, name: str, normalized: float) -> None:
        """Move a named joint using normalized [-1, 1] → [min_us, max_us]."""
        spec = self._get_spec(name)
        self.set_joint_us(name, spec.to_us(normalized))

    def set_channel_us(self, channel: int, pulse_us: float) -> None:
        """Move a channel directly by pulse width (µs); bypasses named joint lookup."""
        self._bridge.set_channel(channel, pulse_us)
        self._state[channel] = pulse_us

    def get_joint_us(self, name: str) -> Optional[float]:
        spec = self._get_spec(name)
        return self._state.get(spec.channel)

    def get_joint_normalized(self, name: str) -> Optional[float]:
        us = self.get_joint_us(name)
        if us is None:
            return None
        return self._get_spec(name).to_normalized(us)

    # ------------------------------------------------------------------
    # High-level motions

    def center(self) -> None:
        """Move all joints to their center_us positions."""
        for name, spec in self.layout.all_joints.items():
            self.set_joint_us(name, spec.center_us)

    def center_all_channels(self) -> None:
        """Broadcast CENTER command — all 16 PCA9685 channels go to 1500 µs."""
        self._bridge.center_all()
        for spec in self.layout.all_joints.values():
            self._state[spec.channel] = 1500.0

    def move_ramp(
        self,
        target: Dict[str, float],
        duration_s: float = 0.5,
        steps: int = 25,
        normalized: bool = True,
    ) -> None:
        """
        Smoothly interpolate multiple joints simultaneously.

        Args:
            target: {joint_name: value} — normalized if normalized=True, else µs.
            duration_s: total motion time.
            steps: number of interpolation steps.
            normalized: interpret values as normalized [-1,1] if True, else µs.
        """
        specs = {name: self._get_spec(name) for name in target}

        # Resolve start and target pulse widths.
        start_us: Dict[str, float] = {}
        target_us: Dict[str, float] = {}
        for name, spec in specs.items():
            start_us[name] = self._state.get(spec.channel, spec.center_us)
            raw = target[name]
            target_us[name] = spec.clamp(spec.to_us(raw) if normalized else raw)

        duration_s = max(0.02, float(duration_s))
        steps = max(2, int(steps))
        dt = duration_s / steps

        for i in range(1, steps + 1):
            t = i / float(steps)
            alpha = t * t * (3.0 - 2.0 * t)  # smoothstep
            for name, spec in specs.items():
                us = start_us[name] + (target_us[name] - start_us[name]) * alpha
                self._bridge.set_channel(spec.channel, us)
                self._state[spec.channel] = us
            time.sleep(dt)

    # ------------------------------------------------------------------

    def _get_spec(self, name: str) -> JointSpec:
        joints = self.layout.all_joints
        if name not in joints:
            raise ValueError(f"Unknown joint {name!r}. Available: {list(joints)}")
        return joints[name]
