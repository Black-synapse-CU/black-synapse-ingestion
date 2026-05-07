"""
Interactive servo calibration REPL.

Run:
    python -m robotic_arm.calibrate --port COM3

Commands:
    <ch> <pulse>          send pulse (µs) to channel, e.g.  3 1500
    center                send 1500 µs to all 16 channels
    log <ch> <pulse> <angle>   record a calibration point
    show                  print recorded calibration points
    save                  write calibration points to calibration.json
    quit / q              exit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from robotic_arm.serial_bridge import SerialBridge

CHANNEL_NAMES = {
    0: "base",
    1: "shoulder1",
    2: "wrist_tilt",
    3: "shoulder2",
    4: "wrist_rotate",
    5: "elbow",
}

# Records: list of {"channel": int, "name": str, "pulse_us": int, "angle_deg": float}
_log: list[dict] = []


def _ch_label(ch: int) -> str:
    name = CHANNEL_NAMES.get(ch, "?")
    return f"ch{ch} ({name})"


def run(bridge: SerialBridge) -> None:
    print("\nServo Calibration REPL")
    print("  <ch> <pulse>               → move joint")
    print("  center                     → all channels to 1500 µs")
    print("  log <ch> <pulse> <angle>   → record this position")
    print("  show                       → print log")
    print("  save                       → write calibration.json")
    print("  quit / q                   → exit\n")

    while True:
        try:
            raw = input("cal> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ("quit", "q"):
            break

        elif cmd == "center":
            bridge.center_all()
            print("  → all channels → 1500 µs")

        elif cmd == "show":
            if not _log:
                print("  (no points logged yet)")
            for p in _log:
                print(f"  ch{p['channel']} ({p['name']})  {p['pulse_us']} µs  →  {p['angle_deg']}°")

        elif cmd == "save":
            out = Path("robotic_arm/calibration.json")
            out.write_text(json.dumps(_log, indent=2))
            print(f"  saved {len(_log)} points → {out}")

        elif cmd == "log":
            if len(parts) != 4:
                print("  usage: log <ch> <pulse> <angle>")
                continue
            try:
                ch = int(parts[1])
                pulse = int(parts[2])
                angle = float(parts[3])
            except ValueError:
                print("  bad values — ch and pulse must be integers, angle can be float")
                continue
            _log.append({
                "channel": ch,
                "name": CHANNEL_NAMES.get(ch, "unknown"),
                "pulse_us": pulse,
                "angle_deg": angle,
            })
            print(f"  logged: {_ch_label(ch)}  {pulse} µs  →  {angle}°")

        else:
            # Try to parse as "<ch> <pulse>"
            if len(parts) == 2:
                try:
                    ch = int(parts[0])
                    pulse = int(parts[1])
                except ValueError:
                    print("  unknown command")
                    continue
                if not (0 <= ch <= 15):
                    print("  channel must be 0–15")
                    continue
                if not (400 <= pulse <= 2700):
                    print("  pulse must be 400–2700 µs")
                    continue
                bridge.set_channel(ch, pulse)
                print(f"  → {_ch_label(ch)}  {pulse} µs")
            else:
                print("  unknown command")


def main() -> None:
    parser = argparse.ArgumentParser(description="Servo calibration REPL")
    parser.add_argument("--port", required=True, help="Serial port, e.g. COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--skip-ready", action="store_true")
    args = parser.parse_args()

    bridge = SerialBridge(args.port, args.baud, require_ready=not args.skip_ready)
    print(f"Connecting to {args.port}…")
    bridge.open()
    print("Connected.\n")

    try:
        run(bridge)
    finally:
        bridge.close()
        print("Disconnected.")


if __name__ == "__main__":
    main()
