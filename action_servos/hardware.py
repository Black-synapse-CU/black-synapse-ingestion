"""PCA9685 PWM driver over Linux I2C (e.g. Jetson /dev/i2c-*)."""

from __future__ import annotations

import errno
import logging
import math
import time
from typing import Optional

try:
    from smbus2 import SMBus
except ImportError:  # not available on macOS / non-Jetson dev machines
    SMBus = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

MODE1 = 0x00
MODE2 = 0x01
PRESCALE = 0xFE
LED0_ON_L = 0x06

MODE1_SLEEP = 0x10
MODE1_RESTART = 0x80
MODE1_AI = 0x20

# Internal osc used when EXTCLK not wired (typical breakout).
OSC_HZ = 25_000_000.0


class PCA9685:
    def __init__(
        self,
        bus: int,
        address: int,
        frequency_hz: float = 50.0,
    ) -> None:
        self._bus_no = bus
        self._address = address
        self._frequency_hz = frequency_hz
        self._bus: Optional[SMBus] = None

    def open(self) -> None:
        if self._bus is not None:
            return
        if SMBus is None:
            raise OSError("smbus2 is not installed — hardware access requires a Linux/Jetson device")
        dev = f"/dev/i2c-{self._bus_no}"
        try:
            self._bus = SMBus(self._bus_no)
        except OSError as e:
            if e.errno == errno.EBUSY:
                raise OSError(
                    errno.EBUSY,
                    f"{e.strerror} on {dev}: another process likely holds the bus, or a kernel "
                    f"driver (e.g. pca9685) claimed it. Try: sudo fuser -v {dev} ; stop duplicate "
                    "servo scripts; only one SMBus user at a time per bus.",
                ) from e
            raise
        self._reset()
        self.set_pwm_frequency(self._frequency_hz)
        logger.debug("PCA9685 opened bus=%s addr=0x%02x", self._bus_no, self._address)

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None

    def __enter__(self) -> PCA9685:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def frequency_hz(self) -> float:
        return self._frequency_hz

    def _read8(self, reg: int) -> int:
        assert self._bus is not None
        return self._bus.read_byte_data(self._address, reg) & 0xFF

    def _write8(self, reg: int, value: int) -> None:
        assert self._bus is not None
        self._bus.write_byte_data(self._address, reg, value & 0xFF)

    def _reset(self) -> None:
        self._write8(MODE1, 0x00)
        time.sleep(0.01)

    def set_pwm_frequency(self, hz: float) -> None:
        if hz < 23.85 or hz > 1525.88:
            raise ValueError("PCA9685 practical range ~24–1526 Hz")
        prescale_float = OSC_HZ / (4096.0 * hz) - 1.0
        prescale = int(math.floor(prescale_float + 0.5))
        prescale = max(3, min(255, prescale))

        oldmode = self._read8(MODE1)
        newmode = (oldmode & 0x7F) | MODE1_SLEEP
        self._write8(MODE1, newmode)
        time.sleep(0.005)
        self._write8(PRESCALE, prescale)
        self._write8(MODE1, oldmode | MODE1_AI)
        time.sleep(0.005)
        self._write8(MODE1, oldmode | MODE1_AI | MODE1_RESTART)
        self._frequency_hz = OSC_HZ / (4096.0 * float(prescale + 1))
        logger.debug("PCA9685 prescale=%s effective_hz=%.3f", prescale, self._frequency_hz)

    def set_channel_pulse_us(self, channel: int, pulse_us: float) -> None:
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be 0-15, got {channel}")
        pulse_us = max(0.0, float(pulse_us))
        off_count = int(round(pulse_us * 4096.0 * self._frequency_hz / 1_000_000.0))
        off_count = min(4095, max(0, off_count))
        self._set_channel_pwm(channel, 0, off_count)

    def _set_channel_pwm(self, channel: int, on: int, off: int) -> None:
        assert self._bus is not None
        on &= 0xFFF
        off &= 0xFFF
        base = LED0_ON_L + 4 * channel
        data = [
            on & 0xFF,
            (on >> 8) & 0xFF,
            off & 0xFF,
            (off >> 8) & 0xFF,
        ]
        self._bus.write_i2c_block_data(self._address, base, data)

    def read_channel_raw(self, channel: int) -> tuple[int, int]:
        """Read back ON and OFF counts (12-bit) for a channel."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be 0-15, got {channel}")
        assert self._bus is not None
        base = LED0_ON_L + 4 * channel
        data = self._bus.read_i2c_block_data(self._address, base, 4)
        on = data[0] | ((data[1] & 0x0F) << 8)
        off = data[2] | ((data[3] & 0x0F) << 8)
        return on, off

    def reset_channel(self, channel: int, pulse_us: float) -> None:
        """Wake the chip if sleeping, clear any FULL_OFF state, and re-send pulse_us."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be 0-15, got {channel}")
        assert self._bus is not None
        mode = self._read8(MODE1)
        if mode & MODE1_SLEEP:
            self._write8(MODE1, mode & ~MODE1_SLEEP)
            time.sleep(0.005)
            self._write8(MODE1, (mode & ~MODE1_SLEEP) | MODE1_RESTART)
        base = LED0_ON_L + 4 * channel
        self._bus.write_i2c_block_data(self._address, base, [0x00, 0x00, 0x00, 0x00])
        self.set_channel_pulse_us(channel, pulse_us)
        logger.debug("reset_channel ch=%d pulse_us=%.1f", channel, pulse_us)

    def set_channel_full_off(self, channel: int) -> None:
        """Drive channel output permanently LOW (servo torque off, no PWM signal)."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be 0-15, got {channel}")
        assert self._bus is not None
        base = LED0_ON_L + 4 * channel
        # Setting bit 4 of OFF_H (0x10) activates the FULL_OFF override
        self._bus.write_i2c_block_data(self._address, base, [0x00, 0x00, 0x00, 0x10])

    def sleep_all(self) -> None:
        """Sleep the chip (stops PWM outputs)."""
        mode = self._read8(MODE1)
        self._write8(MODE1, mode | MODE1_SLEEP)

    def wake(self) -> None:
        mode = self._read8(MODE1)
        self._write8(MODE1, mode & ~MODE1_SLEEP)
        time.sleep(0.005)
        self._write8(MODE1, mode & ~MODE1_SLEEP | MODE1_RESTART)
