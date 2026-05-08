"""
Raw pulse visualizer for the robotic arm.

Run from the repo root:
    python -m robotic_arm.visualizer
    python -m robotic_arm.visualizer --port COM3   # also drives real hardware

Dependencies:
    pip install matplotlib numpy pyserial
"""

from __future__ import annotations

import argparse
import tkinter as tk
from tkinter import ttk
from typing import Optional

_BG     = "#1e1e1e"
_FG     = "#cccccc"
_ACCENT = "#3a7bd5"

PULSE_MIN = 400
PULSE_MAX = 2700

# (channel, label, start_us)
CHANNELS = [
    (0, "Base",         800),
    (1, "Shoulder 1",  1300),
    (2, "Wrist Tilt",  1500),
    (3, "Shoulder 2",  1500),
    (4, "Wrist Rotate",1500),
    (5, "Elbow",        400),
]


class ArmVisualizer:
    def __init__(self, bridge=None) -> None:
        self._bridge = bridge

        self.root = tk.Tk()
        self.root.title("Robotic Arm — Raw Pulse Control")
        self.root.configure(bg=_BG)

        self._vars: dict[int, tk.DoubleVar] = {}
        self._build_ui()

        if self._bridge:
            self._send_all()

    def _build_ui(self) -> None:
        frame = tk.Frame(self.root, bg=_BG, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Raw Pulse Control (µs)", fg="#ffffff", bg=_BG,
                 font=("Helvetica", 14, "bold")).pack(anchor="w", pady=(0, 12))

        for ch, name, start in CHANNELS:
            var = tk.DoubleVar(value=float(start))
            self._vars[ch] = var
            self._slider_row(frame, ch, name, var)

        ttk.Separator(frame, orient="horizontal").pack(fill=tk.X, pady=16)

        btn_frame = tk.Frame(frame, bg=_BG)
        btn_frame.pack(fill=tk.X)

        tk.Button(
            btn_frame, text="Reset to Start",
            bg="#444444", fg="white", font=("Helvetica", 10), relief=tk.FLAT,
            padx=10, pady=6, command=self._center_all,
        ).pack(side=tk.LEFT, padx=(0, 8))

        self._status_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=self._status_var, fg="#ffcc00", bg=_BG,
                 font=("Helvetica", 9), anchor="w").pack(anchor="w", pady=(8, 0))

    def _slider_row(self, parent, ch: int, name: str, var: tk.DoubleVar) -> None:
        row = tk.Frame(parent, bg=_BG)
        row.pack(fill=tk.X, pady=6)

        tk.Label(row, text=f"ch{ch}  {name}", fg=_FG, bg=_BG,
                 font=("Helvetica", 10), width=16, anchor="w").pack(side=tk.LEFT)

        val_lbl = tk.Label(row, text=f"{int(var.get())} µs", fg="#ffffff", bg=_BG,
                           font=("Helvetica", 10, "bold"), width=9, anchor="e")
        val_lbl.pack(side=tk.RIGHT)

        def on_move(v, vl=val_lbl, c=ch):
            vl.config(text=f"{int(float(v))} µs")
            if self._bridge:
                try:
                    self._bridge.set_channel(c, float(v))
                    self._status_var.set("")
                except Exception as exc:
                    self._status_var.set(f"Hardware error: {exc}")

        tk.Scale(
            row, variable=var, from_=PULSE_MIN, to=PULSE_MAX,
            orient=tk.HORIZONTAL, resolution=1, showvalue=False,
            bg=_BG, troughcolor="#3a3a3a", activebackground=_ACCENT,
            highlightthickness=0, length=400, command=on_move,
        ).pack(side=tk.LEFT, padx=8)

    def _center_all(self) -> None:
        for ch, _, start in CHANNELS:
            self._vars[ch].set(float(start))
        if self._bridge:
            self._send_all()

    def _send_all(self) -> None:
        try:
            for ch, var in self._vars.items():
                self._bridge.set_channel(ch, var.get())
        except Exception as exc:
            self._status_var.set(f"Hardware error: {exc}")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Robotic arm raw pulse visualizer")
    parser.add_argument("--hardware", action="store_true",
                        help="Drive real servos via I2C (requires PCA9685 connected)")
    parser.add_argument("--i2c-address", type=lambda x: int(x, 0), default=0x40,
                        help="PCA9685 I2C address (default: 0x40)")
    args = parser.parse_args()

    bridge = None
    if args.hardware:
        from robotic_arm.i2c_bridge import I2CBridge
        bridge = I2CBridge(address=args.i2c_address)
        bridge.open()

    try:
        ArmVisualizer(bridge=bridge).run()
    finally:
        if bridge:
            bridge.close()


if __name__ == "__main__":
    main()
