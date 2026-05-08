"""Defaults for I2C, PCA9685, and per-joint channel maps (adjust to your wiring)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Typical Jetson header I2C bus; confirm with `i2cdetect -l` on device.
DEFAULT_I2C_BUS = 7
DEFAULT_PCA9685_ADDRESS = 0x40
DEFAULT_PWM_FREQUENCY_HZ = 50.0

# Standard hobby servo pulse range (microseconds); tune per servo in JointSpec.
DEFAULT_MIN_US = 900
DEFAULT_MAX_US = 2100

# Per-servo-type safe pulse limits (µs)
DS3225_MIN_US: float = 500   # 25 kg·cm high-torque digital servo
DS3225_MAX_US: float = 2500
MG995_MIN_US:  float = 500   # standard metal-gear servo
MG995_MAX_US:  float = 2500
SG90_MIN_US:   float = 600   # micro servo
SG90_MAX_US:   float = 2400


@dataclass(frozen=True)
class JointSpec:
    """Maps a logical joint to a PCA9685 channel and safe pulse limits."""

    channel: int
    min_us: float = DEFAULT_MIN_US
    max_us: float = DEFAULT_MAX_US
    center_us: float = 1500.0

    def __post_init__(self) -> None:
        if not 0 <= self.channel <= 15:
            raise ValueError(f"PCA9685 channel must be 0-15, got {self.channel}")
        if self.min_us >= self.max_us:
            raise ValueError("min_us must be < max_us")


@dataclass(frozen=True)
class ServoLayout:
    # Arm — required
    base:           JointSpec
    shoulder_a:     JointSpec
    shoulder_b:     JointSpec
    elbow:          JointSpec

    # Arm — optional behaviour flag
    shoulder_b_inv: bool = True

    # Arm — optional extra joints
    wrist_tilt:     Optional[JointSpec] = None

    # Head / ear — optional
    head_pan:       Optional[JointSpec] = None
    head_tilt:      Optional[JointSpec] = None
    ear:            Optional[JointSpec] = None

    @classmethod
    def default_layout(cls) -> ServoLayout:
        return cls(
            base=JointSpec(0),
            shoulder_a=JointSpec(1, min_us=1500.0, max_us=2300.0, center_us=1900.0),
            shoulder_b=JointSpec(3, min_us=1500.0, max_us=2300.0, center_us=1900.0),
            wrist_tilt=JointSpec(4),
            elbow=JointSpec(7),
            shoulder_b_inv=False,
            head_pan=JointSpec(5,  min_us=1000.0, max_us=2500.0, center_us=1700.0),
            head_tilt=JointSpec(6, min_us=1200.0, max_us=2200.0, center_us=1700.0),
        )
