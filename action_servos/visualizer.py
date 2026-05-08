"""
Tkinter pulse-width visualizer for action_servos.

Run from repo root:
    python -m action_servos.visualizer                  # simulation only
    python -m action_servos.visualizer --hardware       # drive real hardware
    python -m action_servos.visualizer --hardware --bus 7
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from tkinter import ttk
from typing import Optional

from action_servos.config import (
    DEFAULT_I2C_BUS,
    DEFAULT_PCA9685_ADDRESS,
    DEFAULT_PWM_FREQUENCY_HZ,
    JointSpec,
    ServoLayout,
)
from action_servos.groups import ServoOrchestrator, us_to_normalized

_BG     = "#1e1e1e"
_FG     = "#cccccc"
_ACCENT = "#3a7bd5"
_SEC    = "#888888"


class ServoVisualizer:
    def __init__(
        self,
        orch: Optional[ServoOrchestrator] = None,
        layout: Optional[ServoLayout] = None,
    ) -> None:
        self._orch   = orch
        self._layout = layout or ServoLayout.default_layout()
        self._vars:        dict[str, tk.DoubleVar] = {}
        self._norm_labels: dict[str, tk.Label]     = {}

        self.root = tk.Tk()
        self.root.title("action_servos — Servo Visualizer")
        self.root.configure(bg=_BG)
        self.root.resizable(False, False)

        self._status_var = tk.StringVar(
            value="Simulation mode" if orch is None else "Hardware connected"
        )
        self._build_ui()

        if orch is not None:
            self._send_all()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=_BG, padx=24, pady=20)
        outer.pack(fill=tk.BOTH, expand=True)

        tk.Label(outer, text="Servo Visualizer", fg="#ffffff", bg=_BG,
                 font=("Helvetica", 14, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(outer, text="action_servos / PCA9685", fg=_SEC, bg=_BG,
                 font=("Helvetica", 9)).pack(anchor="w", pady=(0, 14))

        L = self._layout

        self._section(outer, "ARM")
        self._joint_row(outer, "base",     L.base,      L.base.center_us)
        self._joint_row(outer, "shoulder", L.shoulder_a, L.shoulder_a.center_us)
        self._joint_row(outer, "elbow",    L.elbow,     L.elbow.center_us)

        if L.head_tilt is not None:
            ttk.Separator(outer, orient="horizontal").pack(fill=tk.X, pady=12)
            self._section(outer, "HEAD")
            if L.head_pan is not None:
                self._joint_row(outer, "head_pan",  L.head_pan,  L.head_pan.center_us)
            self._joint_row(outer, "head_tilt", L.head_tilt, L.head_tilt.center_us)

        if L.ear is not None:
            ttk.Separator(outer, orient="horizontal").pack(fill=tk.X, pady=12)
            self._section(outer, "EAR")
            self._joint_row(outer, "ear", L.ear, L.ear.center_us)

        ttk.Separator(outer, orient="horizontal").pack(fill=tk.X, pady=14)

        btn_row = tk.Frame(outer, bg=_BG)
        btn_row.pack(fill=tk.X)
        self._btn(btn_row, "Center All", self._center_all)
        if self._orch is not None:
            self._btn(btn_row, "Release", self._release)
            self._btn(btn_row, "Resume",  self._resume)

        tk.Label(outer, textvariable=self._status_var, fg="#ffcc00", bg=_BG,
                 font=("Helvetica", 9), anchor="w").pack(anchor="w", pady=(10, 0))

    def _section(self, parent: tk.Frame, title: str) -> None:
        tk.Label(parent, text=title, fg=_SEC, bg=_BG,
                 font=("Helvetica", 9, "bold")).pack(anchor="w", pady=(0, 4))

    def _btn(self, parent: tk.Frame, text: str, cmd) -> None:
        tk.Button(
            parent, text=text, bg="#444444", fg="white",
            font=("Helvetica", 10), relief=tk.FLAT,
            padx=10, pady=6, command=cmd,
        ).pack(side=tk.LEFT, padx=(0, 8))

    def _joint_row(self, parent: tk.Frame, name: str, spec: JointSpec, start_us: float) -> None:
        var = tk.DoubleVar(value=float(start_us))
        self._vars[name] = var

        row = tk.Frame(parent, bg=_BG)
        row.pack(fill=tk.X, pady=5)

        tk.Label(row, text=f"ch{spec.channel}  {name}", fg=_FG, bg=_BG,
                 font=("Helvetica", 10), width=16, anchor="w").pack(side=tk.LEFT)

        us_lbl = tk.Label(row, text=f"{int(start_us)} µs", fg="#ffffff", bg=_BG,
                          font=("Helvetica", 10, "bold"), width=9, anchor="e")
        us_lbl.pack(side=tk.RIGHT)

        norm_lbl = tk.Label(row, text=f"{us_to_normalized(spec, start_us):+.2f}",
                            fg=_ACCENT, bg=_BG, font=("Helvetica", 9), width=6, anchor="e")
        norm_lbl.pack(side=tk.RIGHT, padx=(0, 4))
        self._norm_labels[name] = norm_lbl

        def on_move(v, ul=us_lbl, nl=norm_lbl, sp=spec, n=name):
            us = float(v)
            ul.config(text=f"{int(us)} µs")
            nl.config(text=f"{us_to_normalized(sp, us):+.2f}")
            self._send_joint(n)

        tk.Scale(
            row, variable=var,
            from_=spec.min_us, to=spec.max_us,
            orient=tk.HORIZONTAL, resolution=1, showvalue=False,
            bg=_BG, troughcolor="#3a3a3a", activebackground=_ACCENT,
            highlightthickness=0, length=380, command=on_move,
        ).pack(side=tk.LEFT, padx=8)

    # ------------------------------------------------------------------
    # Hardware dispatch
    # ------------------------------------------------------------------

    def _send_joint(self, name: str) -> None:
        if self._orch is None:
            return
        try:
            if name in ("base", "shoulder", "elbow"):
                self._send_arm()
            elif name in ("head_pan", "head_tilt"):
                self._send_head()
            elif name == "ear":
                self._orch.ear.set_pulse(self._vars["ear"].get())
            self._status_var.set("")
        except Exception as exc:
            self._status_var.set(f"Hardware error: {exc}")

    def _send_arm(self) -> None:
        L = self._layout
        b  = self._vars.get("base",     None)
        sh = self._vars.get("shoulder", None)
        el = self._vars.get("elbow",    None)
        self._orch.arm.set_all(  # type: ignore[union-attr]
            b.get()  if b  is not None else L.base.center_us,
            sh.get() if sh is not None else L.shoulder_a.center_us,
            el.get() if el is not None else L.elbow.center_us,
        )

    def _send_head(self) -> None:
        L = self._layout
        pan_var  = self._vars.get("head_pan")
        tilt_var = self._vars.get("head_tilt")
        pan_us   = pan_var.get()  if pan_var  is not None else (L.head_pan.center_us  if L.head_pan  is not None else 0.0)
        tilt_us  = tilt_var.get() if tilt_var is not None else (L.head_tilt.center_us if L.head_tilt is not None else 0.0)
        self._orch.head.set_pulses(pan_us, tilt_us)  # type: ignore[union-attr]

    def _send_all(self) -> None:
        if self._orch is None:
            return
        try:
            self._send_arm()
            L = self._layout
            if L.head_tilt is not None:
                self._send_head()
            if L.ear is not None and "ear" in self._vars:
                self._orch.ear.set_pulse(self._vars["ear"].get())
            self._status_var.set("")
        except Exception as exc:
            self._status_var.set(f"Hardware error: {exc}")

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------

    def _center_all(self) -> None:
        L = self._layout
        centers = {
            "base":      L.base.center_us,
            "shoulder":  L.shoulder_a.center_us,
            "elbow":     L.elbow.center_us,
        }
        if L.head_pan  is not None: centers["head_pan"]  = L.head_pan.center_us
        if L.head_tilt is not None: centers["head_tilt"] = L.head_tilt.center_us
        if L.ear       is not None: centers["ear"]       = L.ear.center_us

        for name, us in centers.items():
            if name in self._vars:
                self._vars[name].set(float(us))
        # _send_all via on_move callbacks already fired; no extra call needed

    def _release(self) -> None:
        if self._orch is None:
            return
        try:
            self._orch.release_all()
            self._status_var.set("Released — servos limp")
        except Exception as exc:
            self._status_var.set(f"Hardware error: {exc}")

    def _resume(self) -> None:
        if self._orch is None:
            return
        try:
            self._orch.resume_all()
            self._status_var.set("")
        except Exception as exc:
            self._status_var.set(f"Hardware error: {exc}")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="action_servos pulse-width visualizer")
    parser.add_argument("--hardware", action="store_true",
                        help="Drive real servos via I2C (requires PCA9685 connected)")
    parser.add_argument("--bus", type=int, default=DEFAULT_I2C_BUS,
                        help=f"I2C bus number (default: {DEFAULT_I2C_BUS})")
    parser.add_argument("--address", type=lambda x: int(x, 0), default=DEFAULT_PCA9685_ADDRESS,
                        help="PCA9685 I2C address (default: 0x40)")
    parser.add_argument("--hz", type=float, default=DEFAULT_PWM_FREQUENCY_HZ,
                        help="PWM frequency in Hz (default: 50)")
    args = parser.parse_args()

    orch: Optional[ServoOrchestrator] = None
    if args.hardware:
        orch = ServoOrchestrator()
        try:
            orch.open(args.bus, args.address, args.hz)
        except OSError as e:
            print(f"I2C open failed (bus {args.bus} addr 0x{args.address:02x}): {e}",
                  file=sys.stderr)
            sys.exit(1)

    try:
        ServoVisualizer(orch=orch).run()
    finally:
        if orch is not None:
            orch.close()


if __name__ == "__main__":
    main()
