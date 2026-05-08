"""
Direct I2C bridge to the PCA9685 servo driver.

Replaces serial_bridge.py — no Arduino required. The Jetson talks to the
PCA9685 over I2C using the Adafruit CircuitPython PCA9685 library.

Same public interface as the old SerialBridge:
    set_channel(channel, pulse_us)
    center_all()
    ping() → bool
    open() / close() / context-manager

Requirements:
    pip install adafruit-circuitpython-pca9685

Hardware:
    PCA9685 wired to Jetson I2C (SDA/SCL), default address 0x40.
    Set PWM frequency to 50 Hz for standard hobby servos.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PERIOD_US = 20_000.0  # 50 Hz → 20 ms period
_DUTY_16BIT_MAX = 65535  # adafruit PCA9685 duty_cycle is 16-bit


def _us_to_duty(pulse_us: float) -> int:
    """Convert a pulse width in µs to a 16-bit PCA9685 duty-cycle value."""
    return round(max(0.0, float(pulse_us)) / _PERIOD_US * _DUTY_16BIT_MAX)


class I2CBridgeError(Exception):
    pass


class I2CBridge:
    """
    I2C bridge to a PCA9685 servo driver for the robotic arm.

    Args:
        address:   I2C address of the PCA9685 (default 0x40).
        frequency: PWM frequency in Hz (default 50 for hobby servos).
    """

    def __init__(
        self,
        address: int = 0x40,
        frequency: int = 50,
    ) -> None:
        self._address = address
        self._frequency = frequency
        self._pca = None

    # ------------------------------------------------------------------
    # Lifecycle

    def open(self) -> None:
        if self._pca is not None:
            return
        try:
            import board
            import busio
            from adafruit_pca9685 import PCA9685
        except ImportError as exc:
            raise I2CBridgeError(
                "adafruit-circuitpython-pca9685 is required. "
                "Run: pip install adafruit-circuitpython-pca9685"
            ) from exc

        i2c = busio.I2C(board.SCL, board.SDA)
        self._pca = PCA9685(i2c, address=self._address)
        self._pca.frequency = self._frequency
        logger.debug("I2CBridge open: PCA9685 @ 0x%02X, %d Hz", self._address, self._frequency)

    def close(self) -> None:
        if self._pca is not None:
            try:
                self._pca.deinit()
            except Exception:
                pass
            self._pca = None

    def __enter__(self) -> "I2CBridge":
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Commands

    def set_channel(self, channel: int, pulse_us: float) -> None:
        """Drive a single PCA9685 channel to the given pulse width (µs)."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be 0–15, got {channel}")
        if self._pca is None:
            raise I2CBridgeError("I2C bridge not open — call open() first")
        self._pca.channels[channel].duty_cycle = _us_to_duty(pulse_us)
        logger.debug("ch=%d pulse_us=%.1f", channel, pulse_us)

    def center_all(self) -> None:
        """Move all 16 channels to 1500 µs."""
        if self._pca is None:
            raise I2CBridgeError("I2C bridge not open — call open() first")
        duty = _us_to_duty(1500)
        for ch in range(16):
            self._pca.channels[ch].duty_cycle = duty

    def ping(self) -> bool:
        """Return True if the PCA9685 is reachable over I2C."""
        try:
            if self._pca is None:
                return False
            _ = self._pca.frequency  # lightweight register read
            return True
        except Exception:
            return False
