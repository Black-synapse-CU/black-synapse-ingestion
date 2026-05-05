"""Channel map and joint specs for the Arduino-backed robotic arm."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# Serial port defaults — override via env or pass directly to SerialBridge.
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"  # Windows: "COM3", Linux: "/dev/ttyUSB0"
DEFAULT_BAUD_RATE = 115200
DEFAULT_TIMEOUT_S = 2.0

# Standard hobby servo pulse range (µs) corresponding to 0°–180°.
DEFAULT_MIN_US = 500
DEFAULT_MAX_US = 2500
DEFAULT_CENTER_US = 1500

_PULSE_AT_0_DEG = 500.0
_PULSE_AT_180_DEG = 2500.0


def _deg_to_us(degrees: float) -> float:
    """Convert a servo angle in degrees [0, 180] to a pulse width in µs."""
    return _PULSE_AT_0_DEG + (degrees / 180.0) * (_PULSE_AT_180_DEG - _PULSE_AT_0_DEG)


@dataclass(frozen=True)
class JointSpec:
    """Maps a logical arm joint to a PCA9685 channel on the Arduino."""

    channel: int
    min_us: float = DEFAULT_MIN_US
    max_us: float = DEFAULT_MAX_US
    center_us: float = DEFAULT_CENTER_US
    name: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.channel <= 15:
            raise ValueError(f"PCA9685 channel must be 0-15, got {self.channel}")
        if self.min_us >= self.max_us:
            raise ValueError("min_us must be < max_us")
        if not self.min_us <= self.center_us <= self.max_us:
            raise ValueError("center_us must be within [min_us, max_us]")

    @classmethod
    def from_degrees(
        cls,
        channel: int,
        min_deg: float,
        max_deg: float,
        center_deg: Optional[float] = None,
        name: str = "",
    ) -> "JointSpec":
        """Construct a JointSpec from degree limits instead of raw µs."""
        mid = center_deg if center_deg is not None else (min_deg + max_deg) / 2.0
        return cls(
            channel=channel,
            min_us=_deg_to_us(min_deg),
            max_us=_deg_to_us(max_deg),
            center_us=_deg_to_us(mid),
            name=name,
        )

    def clamp(self, pulse_us: float) -> float:
        return max(self.min_us, min(self.max_us, float(pulse_us)))

    def to_us(self, normalized: float) -> float:
        """Map normalized [-1, 1] → [min_us, max_us]."""
        n = max(-1.0, min(1.0, float(normalized)))
        mid = (self.min_us + self.max_us) / 2.0
        half = (self.max_us - self.min_us) / 2.0
        return mid + n * half

    def to_normalized(self, pulse_us: float) -> float:
        """Inverse of to_us."""
        mid = (self.min_us + self.max_us) / 2.0
        half = (self.max_us - self.min_us) / 2.0
        if half <= 0:
            return 0.0
        return max(-1.0, min(1.0, (float(pulse_us) - mid) / half))


@dataclass
class ArmLayout:
    """
    Full joint map for the robotic arm.

    Channel assignments (hardware-verified):
        0 — base
        1 — shoulder1
        2 — wrist_tilt
        3 — shoulder2
        4 — wrist_rotate
        5 — elbow

    Adjust min_us/max_us per servo — most digital servos are 500–2500 µs,
    but cheap SG90s are often 600–2400 µs.
    """

    base: JointSpec = field(
        default_factory=lambda: JointSpec.from_degrees(0, min_deg=0, max_deg=180, center_deg=9, name="base")
    )
    shoulder1: JointSpec = field(
        default_factory=lambda: JointSpec(1, min_us=500, max_us=2500, name="shoulder1")
    )
    wrist_tilt: JointSpec = field(
        default_factory=lambda: JointSpec(2, min_us=500, max_us=2500, name="wrist_tilt")
    )
    shoulder2: JointSpec = field(
        default_factory=lambda: JointSpec.from_degrees(3, min_deg=0, max_deg=130, center_deg=50, name="shoulder2")
    )
    wrist_rotate: JointSpec = field(
        default_factory=lambda: JointSpec(4, min_us=500, max_us=2500, name="wrist_rotate")
    )
    elbow: JointSpec = field(
        default_factory=lambda: JointSpec.from_degrees(5, min_deg=130, max_deg=180, center_deg=180, name="elbow")
    )

    @property
    def all_joints(self) -> Dict[str, JointSpec]:
        return {
            "base": self.base,
            "shoulder1": self.shoulder1,
            "wrist_tilt": self.wrist_tilt,
            "shoulder2": self.shoulder2,
            "wrist_rotate": self.wrist_rotate,
            "elbow": self.elbow,
        }

    def by_channel(self, channel: int) -> Optional[JointSpec]:
        for spec in self.all_joints.values():
            if spec.channel == channel:
                return spec
        return None
