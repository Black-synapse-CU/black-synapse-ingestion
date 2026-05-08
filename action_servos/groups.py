"""High-level controllers for 6-DOF arm, optional head (pan/tilt), and ear."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from action_servos.config import JointSpec, ServoLayout
from action_servos.hardware import PCA9685

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pulse helpers
# ---------------------------------------------------------------------------

def clamp_pulse(spec: JointSpec, pulse_us: float) -> float:
    return max(spec.min_us, min(spec.max_us, float(pulse_us)))


def normalized_to_us(spec: JointSpec, n: float) -> float:
    """Map n in [-1, 1] to [min_us, max_us] linearly."""
    n = max(-1.0, min(1.0, float(n)))
    mid = (spec.min_us + spec.max_us) / 2.0
    half = (spec.max_us - spec.min_us) / 2.0
    return mid + n * half


def us_to_normalized(spec: JointSpec, pulse_us: Optional[float]) -> float:
    """Inverse of normalized_to_us; missing pulse defaults to 0.0 (centre of range)."""
    if pulse_us is None:
        return 0.0
    mid = (spec.min_us + spec.max_us) / 2.0
    half = (spec.max_us - spec.min_us) / 2.0
    if half <= 0:
        return 0.0
    return max(-1.0, min(1.0, (float(pulse_us) - mid) / half))


# ---------------------------------------------------------------------------
# Arm controller (6 DOF, 7 physical servos)
# ---------------------------------------------------------------------------

@dataclass
class ArmState:
    base:       Optional[float] = None
    shoulder:   Optional[float] = None
    elbow:      Optional[float] = None
    wrist_tilt: Optional[float] = None


class ArmController:
    """Controls base rotation, shoulder (dual), and elbow."""

    def __init__(self, pca: PCA9685, layout: ServoLayout) -> None:
        self._pca = pca
        self._L = layout
        self._state = ArmState()
        self._released: bool = False

    # ── shoulder dual-servo helper ──────────────────────────────────────────

    def _write_shoulder(self, pulse_us: float) -> float:
        """Write to both shoulder servos; returns clamped shoulder_a pulse."""
        L = self._L
        u_a = clamp_pulse(L.shoulder_a, pulse_us)
        self._pca.set_channel_pulse_us(L.shoulder_a.channel, u_a)
        if L.shoulder_b_inv:
            mirror = L.shoulder_b.center_us * 2.0 - u_a
            u_b = clamp_pulse(L.shoulder_b, mirror)
        else:
            u_b = clamp_pulse(L.shoulder_b, pulse_us)
        self._pca.set_channel_pulse_us(L.shoulder_b.channel, u_b)
        return u_a

    # ── public interface ────────────────────────────────────────────────────

    @property
    def last_state(self) -> ArmState:
        return self._state

    def set_all(
        self,
        base_us: float,
        shoulder_us: float,
        elbow_us: float,
    ) -> None:
        L = self._L
        b  = clamp_pulse(L.base,  base_us)
        el = clamp_pulse(L.elbow, elbow_us)

        self._pca.set_channel_pulse_us(L.base.channel, b)
        sh = self._write_shoulder(shoulder_us)
        self._pca.set_channel_pulse_us(L.elbow.channel, el)

        self._state.base     = b
        self._state.shoulder = sh
        self._state.elbow    = el
        logger.debug("arm set_all b=%.0f sh=%.0f el=%.0f", b, sh, el)

    def set_normalized(self, base_n: float, shoulder_n: float, elbow_n: float) -> None:
        L = self._L
        self.set_all(
            normalized_to_us(L.base,       base_n),
            normalized_to_us(L.shoulder_a, shoulder_n),
            normalized_to_us(L.elbow,      elbow_n),
        )

    def center(self) -> None:
        L = self._L
        self.set_all(L.base.center_us, L.shoulder_a.center_us, L.elbow.center_us)
        if L.wrist_tilt is not None:
            self.set_wrist_tilt(L.wrist_tilt.center_us)

    def set_wrist_tilt(self, pulse_us: float) -> None:
        L = self._L
        if L.wrist_tilt is None:
            raise RuntimeError("No wrist_tilt spec in layout")
        u = clamp_pulse(L.wrist_tilt, pulse_us)
        self._pca.set_channel_pulse_us(L.wrist_tilt.channel, u)
        self._state.wrist_tilt = u

    def release(self) -> None:
        """Cut PWM to all arm channels; servos go limp."""
        L = self._L
        specs = [L.base, L.shoulder_a, L.shoulder_b, L.elbow]
        if L.wrist_tilt is not None:
            specs.append(L.wrist_tilt)
        for spec in specs:
            self._pca.set_channel_full_off(spec.channel)
        self._released = True

    def resume(self) -> None:
        """Re-engage torque and restore last known positions (or centre if unknown)."""
        L = self._L
        s = self._state
        self.set_all(
            s.base     if s.base     is not None else L.base.center_us,
            s.shoulder if s.shoulder is not None else L.shoulder_a.center_us,
            s.elbow    if s.elbow    is not None else L.elbow.center_us,
        )
        if L.wrist_tilt is not None:
            self.set_wrist_tilt(s.wrist_tilt if s.wrist_tilt is not None else L.wrist_tilt.center_us)
        self._released = False

    def reset(self) -> None:
        """Wake chip, clear FULL_OFF, and re-send last known positions."""
        L = self._L
        s = self._state

        def _r(spec: JointSpec, val: Optional[float]) -> None:
            self._pca.reset_channel(spec.channel, val if val is not None else spec.center_us)

        _r(L.base,  s.base)
        _r(L.elbow, s.elbow)

        sh = s.shoulder if s.shoulder is not None else L.shoulder_a.center_us
        self._pca.reset_channel(L.shoulder_a.channel, sh)
        if L.shoulder_b_inv:
            mirror = clamp_pulse(L.shoulder_b, L.shoulder_b.center_us * 2.0 - sh)
            self._pca.reset_channel(L.shoulder_b.channel, mirror)
        else:
            self._pca.reset_channel(L.shoulder_b.channel, clamp_pulse(L.shoulder_b, sh))

        self._released = False

    def move_ramp(
        self,
        base_us: float,
        shoulder_us: float,
        elbow_us: float,
        duration_s: float = 0.4,
        steps: int = 20,
    ) -> None:
        """Linearly interpolate all joints to target over duration_s."""
        L = self._L
        s = self._state

        t_b  = clamp_pulse(L.base,       base_us)
        t_sh = clamp_pulse(L.shoulder_a, shoulder_us)
        t_el = clamp_pulse(L.elbow,      elbow_us)

        s_b  = s.base     if s.base     is not None else t_b
        s_sh = s.shoulder if s.shoulder is not None else t_sh
        s_el = s.elbow    if s.elbow    is not None else t_el

        duration_s = max(0.01, float(duration_s))
        steps = max(2, int(steps))
        for i in range(1, steps + 1):
            a = i / float(steps)
            self.set_all(
                s_b  + (t_b  - s_b)  * a,
                s_sh + (t_sh - s_sh) * a,
                s_el + (t_el - s_el) * a,
            )
            time.sleep(duration_s / steps)


# ---------------------------------------------------------------------------
# Head controller (optional — retained for AtlasAI head assembly)
# ---------------------------------------------------------------------------

@dataclass
class HeadState:
    pan:  Optional[float] = None
    tilt: Optional[float] = None


class HeadController:
    def __init__(
        self,
        pca: PCA9685,
        tilt: JointSpec,
        pan: Optional[JointSpec] = None,
    ) -> None:
        self._pca = pca
        self._pan = pan
        self._tilt = tilt
        self._state = HeadState()
        self._released: bool = False

    @property
    def pan_spec(self) -> Optional[JointSpec]:
        return self._pan

    @property
    def tilt_spec(self) -> JointSpec:
        return self._tilt

    @property
    def last_pulses(self) -> Tuple[Optional[float], Optional[float]]:
        return (self._state.pan, self._state.tilt)

    def set_pulses(self, pan_us: float, tilt_us: float) -> None:
        tu = clamp_pulse(self._tilt, tilt_us)
        self._pca.set_channel_pulse_us(self._tilt.channel, tu)
        self._state.tilt = tu
        if self._pan is not None:
            pu = clamp_pulse(self._pan, pan_us)
            self._pca.set_channel_pulse_us(self._pan.channel, pu)
            self._state.pan = pu
        else:
            self._state.pan = None

    def set_normalized(self, pan: float, tilt: float) -> None:
        pan_us = normalized_to_us(self._pan, pan) if self._pan is not None else 0.0
        self.set_pulses(pan_us, normalized_to_us(self._tilt, tilt))

    def center(self) -> None:
        pan_c = self._pan.center_us if self._pan is not None else 0.0
        self.set_pulses(pan_c, self._tilt.center_us)

    def release(self) -> None:
        self._pca.set_channel_full_off(self._tilt.channel)
        if self._pan is not None:
            self._pca.set_channel_full_off(self._pan.channel)
        self._released = True

    def resume(self) -> None:
        tilt = self._state.tilt if self._state.tilt is not None else self._tilt.center_us
        pan  = self._state.pan  if self._state.pan  is not None else (
            self._pan.center_us if self._pan is not None else 0.0
        )
        self.set_pulses(pan, tilt)
        self._released = False

    def reset(self) -> None:
        tilt = self._state.tilt if self._state.tilt is not None else self._tilt.center_us
        self._pca.reset_channel(self._tilt.channel, tilt)
        if self._pan is not None:
            pan = self._state.pan if self._state.pan is not None else self._pan.center_us
            self._pca.reset_channel(self._pan.channel, pan)
        self._released = False

    def move_ramp(
        self,
        pan_us: float,
        tilt_us: float,
        duration_s: float = 0.4,
        steps: int = 20,
    ) -> None:
        tt = clamp_pulse(self._tilt, tilt_us)
        pt = clamp_pulse(self._pan, pan_us) if self._pan is not None else 0.0
        if self._state.tilt is None or (self._pan is not None and self._state.pan is None):
            self.set_pulses(pt, tt)
            return
        st = self._state.tilt
        sp = self._state.pan if self._pan is not None else pt
        duration_s = max(0.01, float(duration_s))
        steps = max(2, int(steps))
        for i in range(1, steps + 1):
            a = i / float(steps)
            new_pan  = sp + (pt - sp) * a if self._pan is not None else pt
            new_tilt = st + (tt - st) * a
            self.set_pulses(new_pan, new_tilt)
            time.sleep(duration_s / steps)


# ---------------------------------------------------------------------------
# Ear controller
# ---------------------------------------------------------------------------

@dataclass
class EarState:
    pulse: Optional[float] = None


class EarController:
    def __init__(self, pca: PCA9685, spec: JointSpec) -> None:
        self._pca = pca
        self._spec = spec
        self._state = EarState()
        self._released: bool = False

    @property
    def spec(self) -> JointSpec:
        return self._spec

    @property
    def last_pulse(self) -> Optional[float]:
        return self._state.pulse

    def set_pulse(self, pulse_us: float) -> None:
        u = clamp_pulse(self._spec, pulse_us)
        self._pca.set_channel_pulse_us(self._spec.channel, u)
        self._state.pulse = u

    def set_normalized(self, n: float) -> None:
        self.set_pulse(normalized_to_us(self._spec, n))

    def center(self) -> None:
        self.set_pulse(self._spec.center_us)

    def release(self) -> None:
        self._pca.set_channel_full_off(self._spec.channel)
        self._released = True

    def resume(self) -> None:
        pulse = self._state.pulse if self._state.pulse is not None else self._spec.center_us
        self.set_pulse(pulse)
        self._released = False

    def reset(self) -> None:
        pulse = self._state.pulse if self._state.pulse is not None else self._spec.center_us
        self._pca.reset_channel(self._spec.channel, pulse)
        self._released = False

    def move_ramp(self, pulse_us: float, duration_s: float = 0.4, steps: int = 20) -> None:
        t = clamp_pulse(self._spec, pulse_us)
        if self._state.pulse is None:
            self.set_pulse(t)
            return
        start = self._state.pulse
        duration_s = max(0.01, float(duration_s))
        steps = max(2, int(steps))
        for i in range(1, steps + 1):
            a = i / float(steps)
            self.set_pulse(start + (t - start) * a)
            time.sleep(duration_s / steps)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class ServoOrchestrator:
    """Shared PCA9685 driving the 6-DOF arm + optional head/ear."""

    def __init__(self, layout: Optional[ServoLayout] = None) -> None:
        self.layout = layout or ServoLayout.default_layout()
        self._pca: Optional[PCA9685] = None
        self._arm_ctl: Optional[ArmController] = None
        self._head_ctl: Optional[HeadController] = None
        self._ear_ctl: Optional[EarController] = None

    def open(self, bus: int, address: int, frequency_hz: float = 50.0) -> None:
        self.close()
        self._pca = PCA9685(bus=bus, address=address, frequency_hz=frequency_hz)
        self._pca.open()
        L = self.layout
        self._arm_ctl = ArmController(self._pca, L)
        if L.head_tilt is not None:
            self._head_ctl = HeadController(self._pca, L.head_tilt, pan=L.head_pan)
        if L.ear is not None:
            self._ear_ctl = EarController(self._pca, L.ear)

    def close(self) -> None:
        self._arm_ctl = None
        self._head_ctl = None
        self._ear_ctl = None
        if self._pca is not None:
            self._pca.close()
            self._pca = None

    def __enter__(self) -> ServoOrchestrator:
        from action_servos.config import (
            DEFAULT_I2C_BUS,
            DEFAULT_PCA9685_ADDRESS,
            DEFAULT_PWM_FREQUENCY_HZ,
        )
        self.open(DEFAULT_I2C_BUS, DEFAULT_PCA9685_ADDRESS, DEFAULT_PWM_FREQUENCY_HZ)
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def pca(self) -> PCA9685:
        if self._pca is None:
            raise RuntimeError("ServoOrchestrator not opened")
        return self._pca

    @property
    def arm(self) -> ArmController:
        if self._arm_ctl is None:
            raise RuntimeError("ServoOrchestrator not opened")
        return self._arm_ctl

    @property
    def head(self) -> HeadController:
        if self._head_ctl is None:
            raise RuntimeError("No head_tilt spec in layout or orchestrator not opened")
        return self._head_ctl

    @property
    def ear(self) -> EarController:
        if self._ear_ctl is None:
            raise RuntimeError("No ear spec in layout or orchestrator not opened")
        return self._ear_ctl

    def all_center(self) -> None:
        self.arm.center()
        if self._head_ctl is not None:
            self._head_ctl.center()
        if self._ear_ctl is not None:
            self._ear_ctl.center()

    def estop_center(self) -> None:
        """Safe pose: all joints to calibrated centre."""
        self.all_center()

    def release_all(self) -> None:
        self.arm.release()
        if self._head_ctl is not None:
            self._head_ctl.release()
        if self._ear_ctl is not None:
            self._ear_ctl.release()

    def resume_all(self) -> None:
        self.arm.resume()
        if self._head_ctl is not None:
            self._head_ctl.resume()
        if self._ear_ctl is not None:
            self._ear_ctl.resume()

    def reset_all(self) -> None:
        self.arm.reset()
        if self._head_ctl is not None:
            self._head_ctl.reset()
        if self._ear_ctl is not None:
            self._ear_ctl.reset()


# ---------------------------------------------------------------------------
# Head pose presets
# ---------------------------------------------------------------------------

def presets_head_pose(name: str) -> Optional[Tuple[float, float]]:
    """Named (pan, tilt) in normalised [-1, 1]."""
    poses = {
        "neutral":    (0.0,  0.0),
        "look_left":  (-0.6, 0.0),
        "look_right": (0.6,  0.0),
        "look_up":    (0.0, -0.5),
        "look_down":  (0.0,  0.5),
    }
    return poses.get(name.lower())
