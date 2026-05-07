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
    """
    6-DOF arm (7 physical servos) + optional head/ear for AtlasAI robot.

    Arm joints
    ----------
    base        — MG995   — pan rotation (Z-axis)
    shoulder_a  — DS3225  — primary shoulder lift
    shoulder_b  — DS3225  — secondary shoulder (mechanically coupled to shoulder_a)
    elbow       — MG995   — forearm lift
    wrist_pitch — SG90    — wrist up / down
    wrist_roll  — SG90    — wrist rotation
    gripper     — SG90    — open / close

    shoulder_b_inv: if True shoulder_b is physically mirrored so it receives
                    (2 × center_us − shoulder_a_pulse) to stay synchronised.

    Head / ear (optional, retained for AtlasAI robot head assembly)
    ---------------------------------------------------------------
    head_pan    — optional pan servo
    head_tilt   — optional tilt servo
    ear         — optional ear servo
    """

    # Arm — required
    base:           JointSpec
    shoulder_a:     JointSpec
    shoulder_b:     JointSpec
    elbow:          JointSpec
    wrist_pitch:    JointSpec
    wrist_roll:     JointSpec
    gripper:        JointSpec

    # Arm — optional behaviour flag
    shoulder_b_inv: bool = True

    # Head / ear — optional
    head_pan:       Optional[JointSpec] = None
    head_tilt:      Optional[JointSpec] = None
    ear:            Optional[JointSpec] = None

    @classmethod
    def default_layout(cls) -> ServoLayout:
        """
        Hardware-verified PCA9685 channel wiring (from robotic_arm/config.py):

          ch 0  base         500–2500 µs, center=9°  (~600 µs)
          ch 1  shoulder_a   500–2500 µs, center=1500 µs
          ch 2  wrist_pitch  500–2500 µs, center=1500 µs  (wrist_tilt)
          ch 3  shoulder_b   900–1900 µs, center=1400 µs  (shoulder2, independent)
          ch 4  wrist_roll   500–2500 µs, center=1500 µs  (wrist_rotate)
          ch 5  elbow        1944–2500 µs, center=2500 µs (130°–180°)
          ch 6  gripper      600–2400 µs, center=1500 µs
        """
        return cls(
            base=JointSpec(0, min_us=500.0,  max_us=2500.0, center_us=600.0),
            shoulder_a=JointSpec(1, min_us=500.0,  max_us=2500.0, center_us=1500.0),
            shoulder_b=JointSpec(3, min_us=900.0,  max_us=1900.0, center_us=1400.0),
            elbow=JointSpec(5, min_us=1944.0, max_us=2500.0, center_us=2500.0),
            wrist_pitch=JointSpec(2, min_us=500.0,  max_us=2500.0, center_us=1500.0),
            wrist_roll=JointSpec(4, min_us=500.0,  max_us=2500.0, center_us=1500.0),
            gripper=JointSpec(6, min_us=SG90_MIN_US, max_us=SG90_MAX_US),
            shoulder_b_inv=False,
        )
