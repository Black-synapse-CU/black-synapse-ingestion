"""
Serial protocol bridge between Python and the Arduino servo driver.

Protocol (newline-terminated ASCII over serial):
  Python → Arduino:
    SET <ch> <pulse_us>     — move channel <ch> to <pulse_us> µs
    CENTER                  — move all channels to 1500 µs
    PING                    — check Arduino is alive

  Arduino → Python:
    OK                      — command succeeded
    ERR <message>           — command failed
    PONG                    — response to PING
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import serial

logger = logging.getLogger(__name__)

_READY_BANNER = "READY"  # sent by Arduino after setup()


class SerialBridgeError(Exception):
    pass


class SerialBridge:
    def __init__(
        self,
        port: str,
        baud: int = 115200,
        timeout_s: float = 2.0,
        require_ready: bool = True,
    ) -> None:
        self._port = port
        self._baud = baud
        self._timeout_s = timeout_s
        self._require_ready = require_ready
        self._conn: Optional[serial.Serial] = None

    def open(self) -> None:
        if self._conn and self._conn.is_open:
            return
        self._conn = serial.Serial(
            port=self._port,
            baudrate=self._baud,
            timeout=self._timeout_s,
        )
        # Arduino resets on serial open; wait for READY banner.
        if self._require_ready:
            self._wait_for_ready()
        else:
            import time; time.sleep(2.0)  # still let Arduino reset
        logger.debug("SerialBridge open: %s @ %d baud", self._port, self._baud)

    def close(self) -> None:
        if self._conn and self._conn.is_open:
            self._conn.close()
        self._conn = None

    def __enter__(self) -> SerialBridge:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _wait_for_ready(self, timeout_s: float = 5.0) -> None:
        """Block until the Arduino sends its READY banner or timeout."""
        assert self._conn is not None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            line = self._conn.readline().decode("ascii", errors="replace").strip()
            if line == _READY_BANNER:
                return
            if line:
                logger.debug("Arduino startup: %s", line)
        raise SerialBridgeError(
            f"Arduino did not send '{_READY_BANNER}' within {timeout_s}s on {self._port}"
        )

    def _send(self, cmd: str) -> str:
        """Send a command line and return the response line."""
        if self._conn is None or not self._conn.is_open:
            raise SerialBridgeError("Serial port not open")
        payload = (cmd.strip() + "\n").encode("ascii")
        self._conn.write(payload)
        self._conn.flush()
        response = self._conn.readline().decode("ascii", errors="replace").strip()
        if not response:
            raise SerialBridgeError(f"No response from Arduino for command: {cmd!r}")
        if response.startswith("ERR"):
            raise SerialBridgeError(f"Arduino error: {response}")
        logger.debug("cmd=%r resp=%r", cmd, response)
        return response

    def set_channel(self, channel: int, pulse_us: float) -> None:
        """Drive a single PCA9685 channel to pulse_us microseconds."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be 0-15, got {channel}")
        pulse_us = max(0.0, float(pulse_us))
        self._send(f"SET {channel} {int(round(pulse_us))}")

    def center_all(self) -> None:
        """Move all 16 channels to 1500 µs."""
        self._send("CENTER")

    def ping(self) -> bool:
        """Return True if Arduino responds with PONG."""
        try:
            resp = self._send("PING")
            return resp == "PONG"
        except SerialBridgeError:
            return False
